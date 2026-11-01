"""解密子进程入口：跑完「解密 + WAL 合并」就退出。

为什么单独开进程：
- 上游解密要读微信进程内存、解 20 个库，耗时几十秒到几分钟，放在服务进程里会把面板卡住；
- 单独进程跑完即退，内存立刻还回去，崩了也不影响面板。

调用方式（由 wechat_todo/decrypt.py 的 run_detached 拉起）：
  python -m wechat_todo.decrypt_worker <结果JSON路径>
  或冻结成 exe 后：WechatAutoOrganize.exe --worker decrypt <结果JSON路径>
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    result = {"ok": False, "stage": "start", "stats": {}, "error": ""}
    try:
        from .config import load
        from .decrypt import decrypt
        from .walsync import sync as wal_sync

        cfg = load()
        result["stage"] = "decrypt"
        decrypt(cfg, log=lambda m: None, force=True)
        result["stage"] = "wal"
        wal_stats = wal_sync(cfg.work_dir, cfg.db_dir, cfg.keys_file, log=lambda m: None)
        result.update(ok=True, stage="done", stats={"wal": wal_stats,
                                                    "work_dir": str(cfg.work_dir)})
    except Exception as exc:  # 结果要回传，不能只崩掉
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()[-1500:]
    if out is not None:
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
