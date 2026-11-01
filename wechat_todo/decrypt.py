"""采集层的解密封装：调用上游 wcdb-key-tool（Windows runtime Config.Cipher 扫描）。

本模块只做三件事：定位 db_storage、跑上游脚本、清理解密目录里的 WAL sidecar。
解密算法本身一行都不自己写——上游已实测在微信 4.2.0.11 上 20/20 通过。
"""
from __future__ import annotations

import contextlib
import io
import runpy
import shutil
import sys
from pathlib import Path

from .config import Config, ConfigError


def _clean_sidecars(work_dir: Path) -> int:
    """删掉解密目录里的 *.db-wal / *.db-shm。

    必要性（实测教训）：只要用读写模式打开过一次解密后的库，SQLite 会留下零长
    -wal 与 -shm，之后所有读取都会看到空 schema（0 张表）。所以每次解密后必须清干净。
    """
    removed = 0
    for pattern in ("*.db-wal", "*.db-shm", "*-wal", "*-shm"):
        for f in work_dir.rglob(pattern):
            if f.is_file():
                f.unlink()
                removed += 1
    return removed


def decrypt(cfg: Config, log=print, force: bool = False) -> Path:
    """跑上游工具解密全库，返回解密目录。"""
    if cfg.db_dir is None or not Path(cfg.db_dir).is_dir():
        raise ConfigError(
            f"找不到微信 db_storage：{cfg.db_dir}。"
            "在 config.toml 的 [collect].db_dir 里显式指定。")
    if not cfg.tool_path.is_file():
        raise ConfigError(
            f"找不到采集工具：{cfg.tool_path}。"
            "把 TANGandXUE/wcdb-key-tool 解到 tools/wcdb-key-tool-main/。")

    if force and cfg.work_dir.exists():
        log(f"[decrypt] 清空旧解密目录 {cfg.work_dir}")
        shutil.rmtree(cfg.work_dir)

    cfg.work_dir.mkdir(parents=True, exist_ok=True)

    # 进程内跑：冻结成 exe 后 sys.executable 就是 exe 自己，子进程路线会原地套娃。
    argv = [str(cfg.tool_path), "extract",
            "--db-dir", str(cfg.db_dir),
            "--output", str(cfg.keys_file),
            "--decrypt"]
    log(f"[decrypt] {' '.join(argv)}")
    buf = io.StringIO()
    old_argv = sys.argv
    sys.argv = argv
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            runpy.run_path(str(cfg.tool_path), run_name="__main__")
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise RuntimeError(f"解密失败（exit={exc.code}）\n{buf.getvalue()[-1500:]}") from exc
    finally:
        sys.argv = old_argv

    removed = _clean_sidecars(cfg.work_dir)
    if removed:
        log(f"[decrypt] 清理 WAL sidecar {removed} 个")
    log("[decrypt] 完成")
    return cfg.work_dir
