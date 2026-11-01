#!/usr/bin/env python
"""单进程启动：FastAPI 同时提供 API 与前端静态产物。"""
from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from wechat_todo.config import RESOURCE_ROOT, load  # noqa: E402
from wechat_todo.server import app  # noqa: E402

if __name__ == "__main__":
    cfg = load()
    dist = RESOURCE_ROOT / "frontend" / "dist"
    if not dist.is_dir():
        print("[提示] frontend/dist 不存在：先 cd frontend && pnpm install && pnpm build")
    url = f"http://{cfg.host}:{cfg.port}"
    print(f"面板地址：{url}")
    if "--open" in sys.argv:
        webbrowser.open(url)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")
