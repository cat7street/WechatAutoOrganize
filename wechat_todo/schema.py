"""跨层数据契约。所有模块只认这里定义的类型。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass(slots=True)
class Message:
    """一条已归一化的聊天消息（只含可喂模型的文本）。"""

    session: str
    session_display: str
    local_id: int
    sort_seq: int
    ts: int
    sender: str
    sender_display: str
    text: str
    attachments: list = field(default_factory=list)

    def line(self) -> str:
        import datetime as _dt
        when = _dt.datetime.fromtimestamp(self.ts).strftime("%m-%d %H:%M")
        body = self.text.strip() if self.text else ""
        if self.attachments:
            extra = " | ".join(
                f"{a.label}{('：' + a.text) if a.text else ''}".strip() for a in self.attachments)
            body = f"{body} {extra}".strip()
        return f"[{self.local_id}] {when} {self.sender_display}: {body}"


@dataclass(slots=True)
class Task:
    """LLM 抽出的一条待办。"""

    title: str
    session: str
    session_display: str
    owner: str = ""
    due: str = ""
    priority: str = "medium"
    quote: str = ""
    msg_ids: list[int] = field(default_factory=list)
    confidence: float = 0.0

    def dedupe_hash(self) -> str:
        norm = " ".join(self.title.lower().split())
        raw = f"{self.session}|{norm}|{self.due}|{self.owner}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class SessionInfo:
    username: str
    display: str
    is_chatroom: bool
    msg_count: int
    last_ts: int
    unread: int
    table: str | None
    whitelisted: bool = False
