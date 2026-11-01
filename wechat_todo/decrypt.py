"""采集层的解密封装：调用上游 wcdb-key-tool（Windows runtime Config.Cipher 扫描）。

本模块只做三件事：定位 db_storage、跑上游脚本、清理解密目录里的 WAL sidecar。
解密算法本身一行都不自己写——上游已实测在微信 4.2.0.11 上 20/20 通过。
"""
from __future__ import annotations

import contextlib
import io
import json
import runpy
import shutil
import subprocess
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
            "上游脚本随仓库内嵌在 vendor/wcdb-key-tool/（MIT 快照），"
            "文件缺失或被改动时用 git checkout -- vendor/wcdb-key-tool 恢复。")

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


CREATE_NO_WINDOW = 0x08000000


def _worker_cmd(result_file: Path) -> list[str]:
    """冻结成 exe 时用自己当 worker（sys.executable 就是 exe），开发时用 -m。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker", "decrypt", str(result_file)]
    return [sys.executable, "-m", "wechat_todo.decrypt_worker", str(result_file)]


def run_detached(cfg: Config, log=print, timeout: int = 1800) -> dict:
    """在后台子进程里跑「解密 + WAL 合并」，解完即退。返回统计。

    子进程用 CREATE_NO_WINDOW 启动，不会弹黑框；结果通过临时 JSON 文件回传。
    """
    result_file = cfg.work_dir.parent / "data" / "decrypt_result.json"
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.unlink(missing_ok=True)

    cmd = _worker_cmd(result_file)
    log(f"[decrypt] 后台进程启动：{' '.join(cmd)}")
    flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              creationflags=flags, timeout=timeout,
                              cwd=str(cfg.work_dir.parent))
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"解密子进程超时（>{timeout}s）") from exc

    if not result_file.is_file():
        tail = (proc.stderr or proc.stdout or "")[-800:]
        raise RuntimeError(f"解密子进程没回传结果（exit={proc.returncode}）\n{tail}")
    data = json.loads(result_file.read_text(encoding="utf-8"))
    if not data.get("ok"):
        raise RuntimeError(f"解密失败（{data.get('stage')}）：{data.get('error')}\n"
                           f"{(data.get('traceback') or '')[-600:]}")
    removed = _clean_sidecars(cfg.work_dir)
    if removed:
        log(f"[decrypt] 清理 WAL sidecar {removed} 个")
    wal = (data.get("stats") or {}).get("wal") or {}
    log(f"[decrypt] 完成（子进程已退出）：WAL 合并 {wal.get('patched', 0)} 个库")
    return data.get("stats") or {}
