"""上游采集脚本的就位逻辑：打包资源 → 可写工作目录。

为什么需要这一步：
- PyInstaller 把脚本塞在 `_MEIPASS`（只解压、路径随进程消失，且内容只读语义）；
- 上游 `find_image_key.py` 会在自己同目录读**并回写** `config.json`（它要 db_dir）；
- 所以统一把它们落到 `<数据目录>/data/upstream/`，并在那儿生成 config.json。
dev 与 exe 走同一条路径，不做特例分支。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import Config, ConfigError

TOOL_NAME = "wcdb_key_tool_windows.py"
IMAGE_SCRIPTS = ("decode_image.py", "find_image_key.py")


def prepare(cfg: Config) -> Path:
    """把上游脚本就位到一个可写目录，返回该目录。"""
    dst = cfg.root / "data" / "upstream"
    dst.mkdir(parents=True, exist_ok=True)

    tool_src = cfg.tool_path
    if not tool_src.is_file():
        raise ConfigError(
            f"找不到采集工具：{tool_src}。"
            "脚本来自 vendor/wcdb-key-tool/（随仓库内嵌），"
            "exe 模式下应已随包内嵌（检查 scripts/build_exe.ps1 的 --add-data）。")
    shutil.copy2(tool_src, dst / TOOL_NAME)

    img_src = cfg.image_tool_dir
    for name in IMAGE_SCRIPTS:
        src = img_src / name
        if src.is_file():
            shutil.copy2(src, dst / name)

    # find_image_key.py 读自己同目录的 config.json 拿 db_dir
    cfg_file = dst / "config.json"
    payload = {
        "db_dir": str(cfg.db_dir) if cfg.db_dir else "",
        "keys_file": str(cfg.keys_file),
        "decrypted_dir": str(cfg.work_dir),
        "wechat_process": "Weixin.exe",
    }
    old = cfg_file.read_text(encoding="utf-8") if cfg_file.is_file() else ""
    if old.strip():
        try:
            merged = json.loads(old)
            merged.update(payload)
            payload = merged
        except json.JSONDecodeError:
            pass  # 旧文件坏了就覆盖成正确的
    cfg_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst
