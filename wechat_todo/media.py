"""媒体消息接入：把图片/文件/引用/合并转发/转账等非文本消息变成模型能读的东西。

设计原则：
- 图片走视觉（解 .dat → base64 塞给多模态模型），解密复用上游 decode_image.py
- 文件走文本抽取（xlsx/docx/pdf/txt/csv/md），本地读完当文本喂
- 其余载体（引用/合并转发/链接/转账/红包/系统撤回）纯 XML 解析成短文本
- 语音：本机没有落盘的音频文件，给元信息占位（时长），不假装能转写
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import re
import runpy
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

# ---- 类型编码：WeChat 4.x 把 appmsg 子类型打包进 local_type 高 32 位 ----
TYPE_TEXT = 1
TYPE_IMAGE = 3
TYPE_VOICE = 34
TYPE_CONTACT_CARD = 42
TYPE_VIDEO = 43
TYPE_EMOJI = 47
TYPE_LOCATION = 48
TYPE_APP = 49
TYPE_SYSTEM = 10000

APP_SUBTYPE = {
    5: "链接", 6: "文件", 8: "表情包", 19: "合并转发", 33: "小程序", 36: "小程序",
    51: "视频号", 53: "接龙", 57: "引用", 62: "视频号直播", 2000: "转账",
    2001: "红包", 2003: "音乐",
}

IMG_MAGIC_KEY = "image_keys"
MAX_IMG_EDGE = 1280


def decode_type(local_type: int) -> tuple[int, int]:
    return local_type & 0xFFFFFFFF, local_type >> 32


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class Attachment:
    """一条非文本消息被归一化后的产物。"""

    kind: str                    # image | file | quote | forward | link | money | system | voice | other
    label: str                   # 给模型看的一行描述
    text: str = ""               # 可读文本（文件正文、引用原文等）
    image_b64: str = ""          # data URL，仅 image 用


@dataclass(slots=True)
class MediaContext:
    """媒体解析需要的环境：账号目录 + 硬链接索引 + 图片密钥。"""

    account_root: Path
    hardlink: dict[str, dict] = field(default_factory=dict)  # md5 -> {file_name,dir1,dir2}
    image_keys: dict | None = None
    upstream_dir: Path | None = None
    image_error: str = ""
    _decoder: object | None = None
    _key_cache: dict | None = None


# ---------------------------------------------------------------- 硬链接索引
def build_hardlink_index(work_dir: Path) -> dict[str, dict]:
    """md5 → (file_name, dir1名, dir2名)，覆盖图片/文件/视频三张表。"""
    import sqlite3

    path = work_dir / "hardlink" / "hardlink.db"
    if not path.is_file():
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        dirs = {rowid: name for rowid, name in
                con.execute("select rowid, username from dir2id")}
        index: dict[str, dict] = {}
        for table, kind in (("image_hardlink_info_v4", "image"),
                            ("file_hardlink_info_v4", "file"),
                            ("video_hardlink_info_v4", "video")):
            try:
                rows = con.execute(
                    f"select md5, file_name, file_size, dir1, dir2 from {table}").fetchall()
            except Exception:
                continue
            for md5, fname, size, d1, d2 in rows:
                index[md5] = {
                    "file_name": fname, "size": size, "kind": kind,
                    "dir1": dirs.get(int(d1)) if d1 is not None else None,
                    "dir2": dirs.get(int(d2)) if d2 is not None else None,
                }
        return index
    finally:
        con.close()


def resolve_media_path(ctx: MediaContext, md5: str) -> Path | None:
    """按账号目录的实际布局找文件：attach/<会话>/<月>/Img/、file/<月>/、video/<月>/。"""
    entry = ctx.hardlink.get(md5)
    if not entry or not entry.get("file_name"):
        return None
    name = entry["file_name"]
    root = ctx.account_root / "msg"
    d1, d2 = entry.get("dir1"), entry.get("dir2")
    cands: list[Path] = []
    if entry["kind"] == "image":
        cands += [root / "attach" / str(d1) / str(d2) / "Img" / name,
                  root / "attach" / str(d1) / str(d2) / name,
                  root / "attach" / str(d1) / name]
    elif entry["kind"] == "file":
        cands += [root / "file" / str(d1) / name, root / "file" / name]
    else:
        cands += [root / "video" / str(d1) / name, root / "video" / name]
    return next((c for c in cands if c.is_file()), None)


# ---------------------------------------------------------------- 图片解密
def _load_upstream(ctx: MediaContext):
    """加载上游 decode_image.py（只加载一次）。"""
    if ctx._decoder is not None:
        return ctx._decoder
    if ctx.upstream_dir is None:
        return None
    path = ctx.upstream_dir / "decode_image.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("upstream_decode_image", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    ctx._decoder = module
    return module


KEY_RE_AES = re.compile(r"AES key:\s*([0-9a-fA-F]{16,32})")
KEY_RE_XOR = re.compile(r"XOR key:\s*0x([0-9a-fA-F]{1,2})")


def _read_upstream_keys(ctx: MediaContext) -> dict | None:
    """密钥权威存放处：上游 tools/wechat-decrypt/config.json 的 image_aes_key /
    image_xor_key（扫描器自己写的，比解析它 stdout 可靠）。"""
    if ctx.upstream_dir is None:
        return None
    cfg_file = ctx.upstream_dir / "config.json"
    if not cfg_file.is_file():
        return None
    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    aes = (data.get("image_aes_key") or "").strip()
    if not aes:
        return None
    xor = data.get("image_xor_key")
    return {"aes": aes[:16], "xor": int(xor) if xor is not None else 0x88}


def image_keys(ctx: MediaContext, cache_file: Path) -> dict | None:
    """图片 AES/XOR 密钥：本地缓存 → 上游 config.json → 跑一次上游内存扫描。"""
    if ctx._key_cache is not None:
        return ctx._key_cache
    if cache_file.is_file():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if data.get("aes") and data.get("xor") is not None:
                ctx._key_cache = data
                return data
        except json.JSONDecodeError:
            pass

    keys = _read_upstream_keys(ctx) or _scan_for_keys(ctx)
    if keys is None:
        return None
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(keys, ensure_ascii=False), encoding="utf-8")
    ctx._key_cache = keys
    return keys


def _scan_for_keys(ctx: MediaContext) -> dict | None:
    """跑上游 find_image_key.py 从微信进程内存取 image AES key，再读它落盘的 config.json。"""
    if ctx.upstream_dir is None:
        ctx.image_error = "未配置上游目录 image_tool_dir"
        return None
    scanner = ctx.upstream_dir / "find_image_key.py"
    if not scanner.is_file():
        ctx.image_error = f"找不到上游扫描器：{scanner}"
        return None

    import contextlib
    import os
    buf = io.StringIO()
    old_argv, old_cwd = sys.argv, os.getcwd()
    sys.path.insert(0, str(ctx.upstream_dir))  # 上游脚本 from config import ...
    sys.argv = [str(scanner)]
    try:
        os.chdir(ctx.upstream_dir)
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            runpy.run_path(str(scanner), run_name="__main__")
    except SystemExit:
        pass
    except Exception as exc:  # 扫描器自身报错：记下来，不吞
        ctx.image_error = f"扫描器执行失败：{type(exc).__name__}: {exc}"
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)
        if sys.path and sys.path[0] == str(ctx.upstream_dir):
            sys.path.pop(0)

    keys = _read_upstream_keys(ctx)
    if keys is None:
        tail = " / ".join(line.strip() for line in buf.getvalue().strip().splitlines()[-3:])
        ctx.image_error = ctx.image_error or f"未从内存中拿到 image AES key（{tail or '无输出'}）"
    return keys


def decode_image(ctx: MediaContext, dat_path: Path, cache_file: Path) -> str:
    """解 .dat → JPEG → data URL。

    同一个图片会有多个变体：`<name>_t.dat`（缩略图，标准 JPEG）、`<name>.dat`（原图，
    可能是微信自有 WXGF 容器 / HEVC，需 HEVC 解码）。这里按变体逐个试，以 PIL 能不能
    打开为唯一判定；全部失败就返回空串并记下原因，绝不把原始 .dat 当图片交付。
    """
    decoder = _load_upstream(ctx)
    if decoder is None:
        ctx.image_error = ctx.image_error or "缺少上游 decode_image.py"
        return ""
    keys = image_keys(ctx, cache_file)
    if not keys:
        return ""

    cands = [dat_path.with_name(dat_path.stem + "_t" + dat_path.suffix),
             dat_path,
             dat_path.with_name(dat_path.stem + "_h" + dat_path.suffix)]
    tried: list[str] = []
    for cand in cands:
        if not cand.is_file():
            continue
        out = cand.with_name(cand.stem + ".dec")
        ok_path, fmt = decoder.v2_decrypt_file(str(cand), str(out),
                                               aes_key=keys["aes"], xor_key=keys["xor"])
        if not ok_path:
            tried.append(f"{cand.name}:解密失败")
            continue
        tried.append(f"{cand.name}:{fmt}")
        try:
            from PIL import Image
            with Image.open(ok_path) as im:
                im = im.convert("RGB")
                im.thumbnail((MAX_IMG_EDGE, MAX_IMG_EDGE))
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=80)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:
            # 不是 PIL 认识的位图（例如 wxgf/HEVC 原图），继续试下一个变体
            continue
        finally:
            try:
                Path(ok_path).unlink(missing_ok=True)
            except OSError:
                pass

    ctx.image_error = ctx.image_error or f"无可用变体（{', '.join(tried) or '文件不存在'}）"
    return ""


# ---------------------------------------------------------------- 文档正文
TEXT_DOC_EXT = {".txt", ".md", ".csv", ".log", ".json", ".xml", ".py", ".js", ".html"}


def extract_doc_text(path: Path, max_chars: int) -> str:
    ext = path.suffix.lower()
    try:
        if ext in TEXT_DOC_EXT:
            return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        if ext in (".xlsx", ".xlsm"):
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True, data_only=True)
            out: list[str] = []
            for ws in wb.worksheets[:3]:
                out.append(f"[工作表 {ws.title}]")
                for row in ws.iter_rows(values_only=True, max_row=40):
                    cells = [str(c) for c in row if c not in (None, "")]
                    if cells:
                        out.append("\t".join(cells))
                    if sum(len(x) for x in out) > max_chars:
                        break
            wb.close()
            return "\n".join(out)[:max_chars]
        if ext == ".docx":
            import docx
            d = docx.Document(str(path))
            body = "\n".join(p.text for p in d.paragraphs if p.text.strip())
            for table in d.tables[:3]:
                for row in table.rows[:20]:
                    body += "\n" + "\t".join(c.text.strip() for c in row.cells)
                    if len(body) > max_chars:
                        break
            return body[:max_chars]
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:10])
            return text[:max_chars]
    except Exception as exc:
        return f"（读取失败：{type(exc).__name__}）"
    return f"（二进制文件，{path.stat().st_size} 字节，暂不解析 {ext or '无扩展名'}）"


# ---------------------------------------------------------------- XML 载体解析
def _xml(text: str) -> ET.Element | None:
    text = text.strip()
    start = text.find("<")
    if start < 0:
        return None
    try:
        return ET.fromstring(text[start:])
    except ET.ParseError:
        return None


def _txt(node: ET.Element | None, tag: str, default: str = "") -> str:
    """先读子元素文本，再退回同名属性（微信很多字段是属性不是子节点）。"""
    if node is None:
        return default
    found = node.find(tag)
    if found is not None and found.text:
        return found.text.strip()
    attr = node.get(tag)
    return attr.strip() if attr else default


def parse_attachment(text: str, local_type: int, ctx: MediaContext, *,
                     max_file_chars: int = 4000, with_images: bool = True,
                     cache_file: Path | None = None) -> Attachment | None:
    """把一条非文本消息解析成 Attachment。认不出来的返回 None（不做兜底编造）。"""
    base, sub = decode_type(local_type)
    body = text.strip()
    # 群聊前缀 "wxid:\n" 已在 scan 层剥掉；这里再兜一次以防单聊/子系统差异
    body = re.sub(r"^[^\n<]{1,64}:\n", "", body)

    if base == TYPE_IMAGE:
        root = _xml(body)
        md5 = _txt(root.find("img") if root is not None else None, "md5")
        if not md5:
            return Attachment(kind="image", label="[图片]", text="")
        if not with_images:
            return Attachment(kind="image", label="[图片]", text="（图片已关闭解析）")
        path = resolve_media_path(ctx, md5)
        url = decode_image(ctx, path, cache_file) if path and cache_file else ""
        if not url:
            why = ctx.image_error or ("未找到 .dat 文件" if path is None else "解密失败")
            return Attachment(kind="image", label="[图片]", text=f"（图片未展开：{why}）")
        return Attachment(kind="image", label="[图片]", image_b64=url)

    if base == TYPE_VOICE:
        root = _xml(body)
        vm = root.find("voicemsg") if root is not None else None
        ms = _txt(vm, "voicelength")
        secs = round(int(ms) / 1000, 1) if ms.isdigit() else 0
        return Attachment(kind="voice", label=f"[语音 {secs} 秒]",
                          text=f"（本机无音频文件，未转写：{secs} 秒）")

    if base == TYPE_VIDEO:
        root = _xml(body)
        vm = root.find("videomsg") if root is not None else None
        length = _txt(vm, "playlength")
        return Attachment(kind="video", label=f"[视频 {length or '?'} 秒]", text="")

    if base == TYPE_EMOJI:
        return Attachment(kind="other", label="[表情]", text="")

    if base == TYPE_SYSTEM:
        root = _xml(body)
        if root is not None and root.get("type") == "revokemsg":
            return Attachment(kind="system", label="[撤回消息]", text=_txt(root.find("revokemsg"), "content"))
        return Attachment(kind="system", label="[系统消息]", text=re.sub(r"<[^>]+>", " ", body)[:200].strip())

    if base == TYPE_APP:
        root = _xml(body)
        appmsg = root.find("appmsg") if root is not None else None
        if appmsg is None:
            return None
        label = APP_SUBTYPE.get(sub, f"appmsg-{sub}")
        title = _txt(appmsg, "title")
        des = _txt(appmsg, "des")

        if sub == 57:  # 引用：把被引用的原文带出来
            refer = appmsg.find("refermsg")
            quoted = _txt(refer, "content")
            quark = _txt(refer, "displayname")
            text = f"引用 {quark}：{quoted}\n→ {title}"
            return Attachment(kind="quote", label=f"[引用 {quark}]", text=text[:600])

        if sub == 19:  # 合并转发：recorditem 里有整段聊天
            record = appmsg.find("recorditem")
            raw = record.text if record is not None and record.text else des
            lines = re.findall(r"<datadesc>(.*?)</datadesc>", raw or "", re.S)
            names = re.findall(r"<sourcename>(.*?)</sourcename>", raw or "", re.S)
            joined = "\n".join(
                f"{n}: {d}" for n, d in zip(names or ["?"] * len(lines), lines)) or (raw or "")
            return Attachment(kind="forward", label=f"[合并转发 {title}]",
                              text=re.sub(r"<[^>]+>", "", joined)[:1200])

        if sub in (2000, 2001):  # 转账 / 红包
            pay = appmsg.find("wcpayinfo")
            amount = _txt(pay, "feedesc") or _txt(pay, "paymemo") or ""
            return Attachment(kind="money", label=f"[{label}]", text=f"{title} {des} {amount}".strip())

        if sub in (5, 33, 36, 51, 62):  # 链接/小程序/视频号（拍一拍也走这里，按文本分流）
            if "拍了拍" in des or "拍了拍" in title:
                return Attachment(kind="pat", label="[拍一拍]", text=(title or des)[:120])
            url = _txt(appmsg, "url")
            return Attachment(kind="link", label=f"[{label}]", text=f"{title}\n{des}\n{url}".strip()[:500])

        if sub == 6 or _txt(appmsg, "appattach"):  # 文件
            attach = appmsg.find("appattach")
            fname = _txt(attach, "filename") or _txt(attach, "title") or title
            md5 = _txt(attach, "filemd5") or _txt(attach, "md5")
            size = _txt(attach, "totallen")
            path = resolve_media_path(ctx, md5) if md5 else None
            if path is None:
                # 没有硬链接记录时，按文件名在 file/<月>/ 里找（同名多份时取最大）
                root_dir = ctx.account_root / "msg" / "file"
                hits = sorted((p for p in root_dir.rglob(fname) if p.is_file()),
                              key=lambda p: p.stat().st_size, reverse=True) if fname else []
                path = hits[0] if hits else None
            body_text = extract_doc_text(path, max_file_chars) if path else "（本机未找到该文件）"
            return Attachment(kind="file", label=f"[文件 {fname}]",
                              text=f"{fname}（{size or '?'} 字节）\n{body_text}")

        return Attachment(kind="other", label=f"[{label}]", text=f"{title}\n{des}".strip()[:400])

    return None


def is_media_type(local_type: int, text_types: list[int]) -> bool:
    base, _ = decode_type(local_type)
    return base not in text_types
