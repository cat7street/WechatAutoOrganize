#!/usr/bin/env python
"""单进程启动 + 后台 worker 双用入口。

正常启动（开面板）：
  WechatAutoOrganize.exe                       按 config.toml 的 [server] 启动，自动开浏览器
  WechatAutoOrganize.exe --no-open             不开浏览器
  WechatAutoOrganize.exe --port 8788           换端口
  WechatAutoOrganize.exe --browser "<浏览器路径>"

内部 worker（由程序自己拉起，不弹黑框、跑完即退）：
  WechatAutoOrganize.exe --worker decrypt <结果json>
"""
from __future__ import annotations

import errno
import socket
import subprocess
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
BASE = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent


def _redirect_output() -> None:
    """打包成无控制台 exe 时 stdout/stderr 是 None，print 会炸；统一写日志文件。"""
    if not FROZEN:
        return
    try:
        log_dir = BASE / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = open(log_dir / "app.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = fh
        sys.stderr = fh
        print("\n" + "=" * 60)
    except OSError:
        pass


_redirect_output()
sys.path.insert(0, str(BASE))

import uvicorn  # noqa: E402

from wechat_todo.config import RESOURCE_ROOT, load  # noqa: E402
from wechat_todo.server import app  # noqa: E402


def _arg(name: str) -> str | None:
    if name in sys.argv:
        idx = sys.argv.index(name)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return None


def _fatal(msg: str) -> None:
    """没有控制台时，用系统弹窗把错误说清楚，别静默退出。"""
    print(msg)
    if FROZEN and sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, msg, "微信待办 - 启动失败", 0x10)
        except Exception:
            pass


def _launch(url: str, browser: str) -> bool:
    if browser:
        exe = Path(browser)
        if not exe.is_file():
            print(f"[启动] 配置的浏览器不存在：{browser}，改用系统默认")
        else:
            try:
                args = [str(exe), url]
                if "chrome" in exe.name.lower() or "edge" in exe.name.lower():
                    args.insert(1, "--new-window")
                subprocess.Popen(args, close_fds=True)
                return True
            except OSError as exc:
                print(f"[启动] 指定浏览器启动失败（{exc}），改用系统默认")
    import webbrowser

    return webbrowser.open(url)


def _open_when_ready(host: str, port: int, url: str, browser: str) -> None:
    """等端口真的能连上再开浏览器，否则会先看到一个“无法访问”的空白页。"""
    import threading
    import time

    def _worker() -> None:
        target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
        for _ in range(100):
            try:
                with socket.create_connection((target, port), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.15)
        ok = _launch(url, browser)
        print(f"[启动] 已在浏览器打开 {url}" if ok else
              f"[启动] 浏览器未能自动打开，请手动访问 {url}")

    threading.Thread(target=_worker, daemon=True).start()


def _run_worker() -> int:
    """解密子进程模式：跑完即退，不启动服务。"""
    rest = [a for a in sys.argv[1:] if a not in ("--worker",)]
    if rest and rest[0] == "decrypt":
        rest = rest[1:]
    sys.argv = ["wechat_todo.decrypt_worker"] + rest
    from wechat_todo.decrypt_worker import main as worker_main

    return worker_main()


def main() -> int:
    if "--worker" in sys.argv:
        return _run_worker()

    cfg = load()
    host = _arg("--host") or cfg.host
    port = int(_arg("--port") or cfg.port)
    browser = _arg("--browser") or cfg.browser
    dist = RESOURCE_ROOT / "frontend" / "dist"
    if not dist.is_dir():
        print("[提示] frontend/dist 不存在：先 cd frontend && pnpm install && pnpm build")

    url = f"http://{host}:{port}"
    print(f"面板地址：{url}")
    print(f"数据目录：{BASE}")
    want_open = ("--open" in sys.argv) or (cfg.open_browser and "--no-open" not in sys.argv)
    print(f"自动开浏览器：{'是' if want_open else '否'}"
          + (f"（{browser or '系统默认'}）" if want_open else ""))
    if want_open:
        _open_when_ready(host, port, url, browser)
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 10048):
            _fatal(f"端口 {port} 已被占用（WinError 10048）。\n\n"
                   f"1) 如果占用者就是本工具，直接访问 {url}\n"
                   f"2) 换个端口：WechatAutoOrganize.exe --port {port + 1}\n"
                   f"3) 改 config.toml 的 [server].port 后重启\n\n"
                   f"查是谁占用：Get-NetTCPConnection -LocalPort {port} -State Listen")
            return 3
        _fatal(f"启动失败：{type(exc).__name__}: {exc}")
        return 4
    except Exception as exc:  # 无控制台时别静默退出
        _fatal(f"启动失败：{type(exc).__name__}: {exc}")
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
