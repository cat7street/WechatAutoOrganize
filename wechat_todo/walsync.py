"""把微信 WAL 里"还没落盘"的新消息合进解密副本。

为什么必须做这一步：
微信 4.x 开着的时候，新消息先写进 `message_0.db-wal`，主库要等 checkpoint 才更新。
上游解密工具只处理主 `.db`，所以直接读解密副本会**看不到最近几分钟到几小时的消息**，
表现为"明明刚发了消息，日志却说无新消息"。

做法：用 SQLCipher 打开「源库 + 它的 -wal」的副本，让 SQLCipher 自己重放 WAL，
再 `sqlcipher_export` 导出一份标准明文 SQLite。这样既不碰微信的文件，也不用自己解 WAL 帧。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

KEY_LITERAL = "PRAGMA key = \"x'{key}'\";"


def _rel_paths(keys_file: Path) -> dict[str, dict]:
    if not keys_file.is_file():
        raise FileNotFoundError(f"缺少密钥文件：{keys_file}（先跑一次解密）")
    data = json.loads(keys_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"密钥文件格式不符：{keys_file}")
    return data


def sync(work_dir: Path, db_dir: Path, keys_file: Path, log=print) -> dict:
    """对每个「源库有非空 -wal」的库，重放 WAL 并重新导出明文副本。"""
    from sqlcipher3 import dbapi2 as sqlcipher  # 延迟导入，缺包时错误更清楚

    stats = {"checked": 0, "patched": 0, "no_wal": 0, "errors": []}
    keys = _rel_paths(keys_file)
    tmp_root = work_dir / ".walsync"
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    tmp_root.mkdir(parents=True, exist_ok=True)

    for rel, info in keys.items():
        if not rel.lower().endswith(".db"):  # keys.json 里还带着 _db_dir 之类的元数据键
            continue
        stats["checked"] += 1
        src = db_dir / rel
        wal = Path(f"{src}-wal")
        if not src.is_file():
            stats["errors"].append(f"{rel}: 源库不存在")
            continue
        if not wal.is_file() or wal.stat().st_size == 0:
            stats["no_wal"] += 1
            continue

        enc = (info or {}).get("enc_key")
        if not enc:
            stats["errors"].append(f"{rel}: 密钥缺失")
            continue

        stage_dir = tmp_root / rel.replace("\\", "/").replace("/", "__")
        stage_dir.mkdir(parents=True, exist_ok=True)
        db_copy = stage_dir / "db"
        shutil.copy2(src, db_copy)
        shutil.copy2(wal, Path(f"{db_copy}-wal"))
        shm = Path(f"{src}-shm")
        if shm.is_file():
            shutil.copy2(shm, Path(f"{db_copy}-shm"))

        out = work_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out_tmp = out.with_suffix(out.suffix + ".tmp")
        if out_tmp.exists():
            out_tmp.unlink()

        try:
            con = sqlcipher.connect(str(db_copy))
            con.execute(KEY_LITERAL.format(key=enc))
            con.execute("select count(*) from sqlite_master").fetchone()  # 触发 WAL 重放
            lit = str(out_tmp).replace("'", "''")
            con.execute(f"ATTACH DATABASE '{lit}' AS plaintext KEY ''")
            con.execute("SELECT sqlcipher_export('plaintext')")
            con.execute("DETACH DATABASE plaintext")
            con.close()
            out_tmp.replace(out)
            stats["patched"] += 1
            log(f"[wal] {rel}: 已合并 WAL（{wal.stat().st_size // 1024} KB）")
        except Exception as exc:
            stats["errors"].append(f"{rel}: {type(exc).__name__}: {exc}")
            if out_tmp.exists():
                out_tmp.unlink(missing_ok=True)
        finally:
            shutil.rmtree(stage_dir, ignore_errors=True)

    shutil.rmtree(tmp_root, ignore_errors=True)
    # 导出的明文是标准 SQLite（无 reserve 区），不会像上游产物那样留下 sidecar 隐患
    for leftover in work_dir.rglob("*.db-wal"):
        leftover.unlink(missing_ok=True)
    for leftover in work_dir.rglob("*.db-shm"):
        leftover.unlink(missing_ok=True)
    return stats
