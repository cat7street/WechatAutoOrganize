"""API 响应模型：让 /openapi.json 长出真实结构，前端类型由此生成、不会漂移。"""
from __future__ import annotations

from pydantic import BaseModel


class HealthOut(BaseModel):
    status: str
    version: str
    decrypted: bool
    llm_configured: bool
    whitelist_count: int
    db_dir: str | None = None
    running: bool


class SettingsOut(BaseModel):
    base_url: str
    model: str
    api_key_masked: str
    response_format: str
    temperature: float
    window_hours: int
    max_chars_per_request: int
    whitelist: list[str]
    text_types: list[int]
    db_dir: str
    work_dir: str
    out_dir: str
    media_images: bool
    media_files: bool
    media_voice: bool
    max_file_chars: int


class LLMPatchOut(BaseModel):
    ok: bool
    api_key_masked: str


class WhitelistOut(BaseModel):
    ok: bool
    whitelist: list[str]


class MediaOut(BaseModel):
    ok: bool
    media_images: bool
    media_files: bool
    media_voice: bool
    max_file_chars: int


class SessionOut(BaseModel):
    username: str
    display: str
    is_chatroom: bool
    msg_count: int
    last_ts: int
    unread: int
    table: str | None = None
    whitelisted: bool


class SessionsOut(BaseModel):
    total: int
    items: list[SessionOut]


class TaskOut(BaseModel):
    id: int
    hash: str
    session: str
    session_display: str
    title: str
    owner: str
    due: str
    priority: str
    quote: str
    msg_ids: list[int]
    confidence: float
    status: str
    created_at: int
    run_id: str | None = None


class TasksOut(BaseModel):
    items: list[TaskOut]


class OkOut(BaseModel):
    ok: bool


class SessionStat(BaseModel):
    session_display: str
    n: int


class DailyStat(BaseModel):
    date: str
    n: int


class StatsOut(BaseModel):
    by_status: dict[str, int]
    by_priority: dict[str, int]
    total_tasks: int
    prompt_tokens: int
    completion_tokens: int
    per_session: list[SessionStat]
    recent_daily: list[DailyStat]


class RunOut(BaseModel):
    id: str
    started_at: int
    finished_at: int | None = None
    ok: int | None = None
    stats: dict
    prompt_tokens: int
    completion_tokens: int


class RunsOut(BaseModel):
    items: list[RunOut]


class DecryptOut(BaseModel):
    ok: bool
    work_dir: str
