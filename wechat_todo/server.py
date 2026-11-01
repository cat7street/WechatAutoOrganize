"""FastAPI 面板后端：配置读写、会话列表、待办、运行与 SSE 实时日志、静态托管。"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import scan
from .api_models import (DecryptOut, HealthOut, LLMPatchOut, OkOut, RunsOut, SessionsOut,
                         SettingsOut, StatsOut, TasksOut, WhitelistOut)
from .config import RESOURCE_ROOT, ROOT, Config, ConfigError, load
from .decrypt import decrypt
from .pipeline import iter_run
from .store import Store

VERSION = "0.1.0"
app = FastAPI(title="WechatAutoOrganize", version=VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


def _cfg() -> Config:
    return load()


# ------------------------- 运行状态（单用户本机，单例足够） -------------------------
class RunState:
    def __init__(self) -> None:
        self.lock = threading.Condition()
        self.events: list[dict] = []
        self.running = False
        self.run_id: str | None = None
        self.finished = False

    def reset(self) -> None:
        with self.lock:
            self.events = []
            self.running = True
            self.finished = False
            self.lock.notify_all()

    def push(self, ev: dict) -> None:
        with self.lock:
            self.events.append(ev)
            if ev.get("type") in ("done", "error"):
                self.running = False
                self.finished = True
            self.lock.notify_all()


STATE = RunState()


def _worker(cfg: Config, skip_decrypt: bool, dry_run: bool) -> None:
    try:
        for ev in iter_run(cfg, skip_decrypt=skip_decrypt, dry_run=dry_run):
            STATE.push(ev)
    except Exception as exc:  # 已经由 iter_run 产出 error 事件的情况不会走到这里
        STATE.push({"type": "error", "msg": f"{type(exc).__name__}: {exc}"})
    finally:
        with STATE.lock:
            STATE.running = False
            STATE.finished = True
            STATE.lock.notify_all()


# ------------------------- 配置读写 -------------------------
def _toml_set(path: Path, section: str, key: str, value) -> None:
    """在 config.toml 的 [section] 里就地改一行，保留其它内容与注释。"""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if isinstance(value, str):
        rendered = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, list):
        rendered = "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in value) + "]"
    else:
        rendered = str(value)

    head = f"[{section}]"
    if head not in text:
        text = text.rstrip() + f"\n\n{head}\n{key} = {rendered}\n"
    else:
        start = text.index(head)
        nxt = text.find("\n[", start + len(head))
        block = text[start:nxt if nxt != -1 else len(text)]
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.M)
        if pattern.search(block):
            block = pattern.sub(f"{key} = {rendered}", block, count=1)
        else:
            block = block.rstrip() + f"\n{key} = {rendered}\n"
        text = text[:start] + block + (text[nxt:] if nxt != -1 else "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _env_set(values: dict[str, str]) -> None:
    """把密钥写进 .env（前端只写不读）。"""
    path = ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    seen = set()
    out = []
    for line in lines:
        k = line.split("=", 1)[0].strip()
        if k in values:
            out.append(f"{k}={values[k]}")
            seen.add(k)
        else:
            out.append(line)
    for k, v in values.items():
        if k not in seen:
            out.append(f"{k}={v}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * 8}{key[-4:]}"


# ------------------------- 模型 -------------------------
class LLMSettings(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    response_format: str | None = None


class WhitelistSettings(BaseModel):
    whitelist: list[str]


class TaskPatch(BaseModel):
    status: str | None = None
    title: str | None = None
    owner: str | None = None
    due: str | None = None
    priority: str | None = None
    quote: str | None = None


class RunRequest(BaseModel):
    skip_decrypt: bool = False
    dry_run: bool = False


# ------------------------- 接口 -------------------------
@app.get("/api/health", response_model=HealthOut)
def health() -> dict:
    cfg = _cfg()
    return {
        "status": "ok",
        "version": VERSION,
        "decrypted": cfg.work_dir.is_dir(),
        "llm_configured": bool(cfg.base_url and cfg.api_key and cfg.model),
        "whitelist_count": len(cfg.whitelist),
        "db_dir": str(cfg.db_dir) if cfg.db_dir else None,
        "running": STATE.running,
    }


@app.get("/api/settings", response_model=SettingsOut)
def get_settings() -> dict:
    cfg = _cfg()
    return {
        "base_url": cfg.base_url,
        "model": cfg.model,
        "api_key_masked": _mask(cfg.api_key),
        "response_format": cfg.response_format,
        "temperature": cfg.temperature,
        "window_hours": cfg.window_hours,
        "max_chars_per_request": cfg.max_chars_per_request,
        "whitelist": cfg.whitelist,
        "text_types": cfg.text_types,
        "db_dir": str(cfg.db_dir) if cfg.db_dir else "",
        "work_dir": str(cfg.work_dir),
        "out_dir": str(cfg.out_dir),
    }


@app.post("/api/settings/llm", response_model=LLMPatchOut)
def set_llm(body: LLMSettings) -> dict:
    path = ROOT / "config.toml"
    env: dict[str, str] = {}
    if body.base_url is not None:
        _toml_set(path, "llm", "base_url", body.base_url)
        env["WECHAT_TODO_BASE_URL"] = body.base_url
    if body.model is not None:
        _toml_set(path, "llm", "model", body.model)
        env["WECHAT_TODO_MODEL"] = body.model
    if body.response_format is not None:
        _toml_set(path, "llm", "response_format", body.response_format)
    if body.api_key:
        env["WECHAT_TODO_API_KEY"] = body.api_key
    if env:
        _env_set(env)
    return {"ok": True, "api_key_masked": _mask(load().api_key)}


@app.post("/api/settings/whitelist", response_model=WhitelistOut)
def set_whitelist(body: WhitelistSettings) -> dict:
    _toml_set(ROOT / "config.toml", "filter", "whitelist", body.whitelist)
    return {"ok": True, "whitelist": load().whitelist}


@app.get("/api/sessions", response_model=SessionsOut)
def sessions() -> dict:
    cfg = _cfg()
    if not cfg.work_dir.is_dir():
        raise HTTPException(409, "尚未解密，先 POST /api/decrypt")
    rows = scan.list_sessions(cfg.work_dir, cfg.whitelist)
    return {"total": len(rows), "items": [asdict(s) for s in rows]}


@app.post("/api/decrypt", response_model=DecryptOut)
def do_decrypt() -> dict:
    cfg = _cfg()
    try:
        decrypt(cfg, log=lambda m: None)
    except (ConfigError, RuntimeError) as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"ok": True, "work_dir": str(cfg.work_dir)}


@app.post("/api/run")
def start_run(body: RunRequest) -> dict:
    if STATE.running:
        raise HTTPException(409, "已有一次运行在进行中")
    cfg = _cfg()
    try:
        cfg.require_whitelist()
        if not body.dry_run:
            cfg.require_llm()
    except ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    STATE.reset()
    threading.Thread(target=_worker, args=(cfg, body.skip_decrypt, body.dry_run),
                     daemon=True).start()
    return {"ok": True}


@app.get("/api/run/events")
def run_events() -> StreamingResponse:
    def gen():
        idx = 0
        while True:
            with STATE.lock:
                while idx >= len(STATE.events) and not STATE.finished:
                    STATE.lock.wait(timeout=15)
                batch = STATE.events[idx:]
                idx = len(STATE.events)
                finished = STATE.finished
            for ev in batch:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if finished and idx >= len(STATE.events):
                yield "event: end\ndata: {}\n\n"
                return
            if not batch:
                yield ": keep-alive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/tasks", response_model=TasksOut)
def tasks(status: str | None = None, limit: int = 500) -> dict:
    store = Store(_cfg().state_db)
    return {"items": store.list_tasks(status=status, limit=limit)}


@app.patch("/api/tasks/{task_id}", response_model=OkOut)
def patch_task(task_id: int, body: TaskPatch) -> dict:
    store = Store(_cfg().state_db)
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if not payload:
        raise HTTPException(400, "没有要修改的字段")
    ok = store.update_task(task_id, **payload)
    if not ok:
        raise HTTPException(404, f"待办 {task_id} 不存在")
    return {"ok": True}


@app.get("/api/stats", response_model=StatsOut)
def stats() -> dict:
    return Store(_cfg().state_db).stats()


@app.get("/api/runs", response_model=RunsOut)
def runs(limit: int = 20) -> dict:
    return {"items": Store(_cfg().state_db).list_runs(limit=limit)}


# ------------------------- 静态产物（vite build 的 dist） -------------------------
DIST = RESOURCE_ROOT / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
