#!/usr/bin/env python
"""单进程启动：FastAPI 同时提供 API 与前端静态产物。

用法：
  WechatAutoOrganize.exe                   # 按 config.toml 的 [server] 启动，并自动开浏览器
  WechatAutoOrganize.exe --no-open         # 不开浏览器
  WechatAutoOrganize.exe --port 8788       # 临时换端口
  WechatAutoOrganize.exe --browser "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"

[server] 里可配：
  host / port / open_browser（默认 true）/ browser（浏览器可执行文件，留空用系统默认）
"""
from __future__ import annotations

import errno
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from wechat_todo.config import RESOURCE_ROOT, load  # noqa: E402
from wechat_todo.server import app  # noqa: E402

FROZEN = getattr(sys, "frozen", False)


def _arg(name: str) -> str | None:
    if name in sys.argv:
        idx = sys.argv.index(name)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return None


def _pause(msg: str = "按回车键退出…") -> None:
    """双击启动时别让窗口一闪就关，否则用户看不到原因。"""
    if FROZEN and sys.stdin and sys.stdin.isatty():
        try:
            input(msg)
        except (EOFError, KeyboardInterrupt):
            pass


def _launch(url: str, browser: str) -> bool:
    """用指定浏览器打开；没指定就走系统默认。返回是否成功发起。"""
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
    return webbrowser.open(url)


def _open_when_ready(host: str, port: int, url: str, browser: str) -> None:
    """等端口真的能连上再开浏览器，否则会先看到一个“无法访问”的空白页。"""

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


def main() -> int:
    cfg = load()
    host = _arg("--host") or cfg.host
    port = int(_arg("--port") or cfg.port)
    browser = _arg("--browser") or cfg.browser
    dist = RESOURCE_ROOT / "frontend" / "dist"
    if not dist.is_dir():
        print("[提示] frontend/dist 不存在：先 cd frontend && pnpm install && pnpm build")

    url = f"http://{host}:{port}"
    print(f"面板地址：{url}")
    print(f"数据目录：{Path.cwd()}")
    want_open = ("--open" in sys.argv) or (cfg.open_browser and "--no-open" not in sys.argv)
    print(f"自动开浏览器：{'是' if want_open else '否'}"
          + (f"（{browser or '系统默认'}）" if want_open else ""))
    if want_open:
        _open_when_ready(host, port, url, browser)
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 10048):
            print("\n" + "=" * 60)
            print(f"[启动失败] 端口 {port} 已被占用（WinError 10048）。")
            print("通常是已经有一个实例在跑，或者开发服务器还开着。")
            print("\n三个办法：")
            print(f"  1) 直接开浏览器访问 {url}（如果占用者就是本工具）")
            print(f"  2) 换个端口：WechatAutoOrganize.exe --port {port + 1}")
            print(f"  3) 改配置文件 [server].port 后重启")
            print("\n查谁占着端口：")
            print(f'  Get-NetTCPConnection -LocalPort {port} -State Listen | '
                  "Select-Object OwningProcess")
            print("=" * 60)
            _pause()
            return 3
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
