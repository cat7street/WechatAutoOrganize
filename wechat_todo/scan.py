"""只读读取解密后的微信库：会话列表、白名单匹配、按水位线取增量消息。

铁律：解密目录里的库一律 mode=ro&immutable=1 打开，绝不以读写模式连接。
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

from .media import (Attachment, MediaContext, build_hardlink_index, decode_type,
                    parse_attachment)
from .schema import Message, SessionInfo

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
# 群聊里 message_content 自带 "发送人:\n" 前缀，最多到冒号前 64 字符
PREFIX_RE = re.compile(r"^[^\n:]{1,64}:\n")
TRAILING_SPACE = "\u2005\u00a0\t "


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"缺少解密库：{db_path}（先跑一次解密）")
    return sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)


def decompress(value):
    """WCDB 会把部分消息体压成 zstd（magic 28 b5 2f fd）。文本类型通常是明文 str。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raw = bytes(value)
    if raw[:4] == ZSTD_MAGIC:
        import zstandard
        return zstandard.ZstdDecompressor().decompress(raw, max_output_size=8 << 20).decode(
            "utf-8", "replace")
    return None


def _display_names(work: Path) -> dict[str, str]:
    """username -> 备注名/昵称。"""
    names: dict[str, str] = {}
    try:
        con = connect(work / "contact" / "contact.db")
    except FileNotFoundError:
        return names
    try:
        for username, remark, nick in con.execute(
                "select username, remark, nick_name from contact"):
            names[username] = (remark or nick or username)
    finally:
        con.close()
    return names


def _sender_map(work: Path) -> dict[int, str]:
    """message_0.db 的 Name2Id: rowid -> username（real_sender_id 就是 rowid）。"""
    con = connect(work / "message" / "message_0.db")
    try:
        return {rowid: name for rowid, name in
                con.execute("select rowid, user_name from Name2Id")}
    finally:
        con.close()


def _table_of(username: str) -> str:
    return "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()


def list_sessions(work: Path, whitelist: list[str] | None = None) -> list[SessionInfo]:
    """全部会话 + 消息量，带白名单标记。whitelist 里任一关键词命中即纳入。"""
    wl = [w.lower() for w in (whitelist or [])]
    displays = _display_names(work)

    mcon = connect(work / "message" / "message_0.db")
    try:
        tables = {r[0] for r in mcon.execute(
            "select name from sqlite_master where type='table' and name like 'Msg_%'")}
        counts: dict[str, int] = {}
        for t in tables:
            counts[t] = mcon.execute(f"select count(*) from {t}").fetchone()[0]
    finally:
        mcon.close()

    out: list[SessionInfo] = []
    scon = connect(work / "session" / "session.db")
    try:
        rows = scon.execute(
            "select username, unread_count, last_timestamp from SessionTable").fetchall()
    finally:
        scon.close()

    for username, unread, last_ts in rows:
        table = _table_of(username)
        hit = table in tables
        display = displays.get(username, username)
        blobs = [username.lower(), display.lower()]
        whitelisted = bool(wl) and any(w in b for w in wl for b in blobs)
        out.append(SessionInfo(
            username=username,
            display=display,
            is_chatroom=username.endswith(("@chatroom", "@openim")),
            msg_count=counts.get(table, 0) if hit else 0,
            last_ts=int(last_ts or 0),
            unread=int(unread or 0),
            table=table if hit else None,
            whitelisted=whitelisted,
        ))
    out.sort(key=lambda s: s.last_ts, reverse=True)
    return out


def fetch_messages(work: Path, session: SessionInfo, after_seq: int, *,
                   text_types: list[int], senders: dict[int, str] | None = None,
                   displays: dict[str, str] | None = None,
                   limit: int = 5000, media: MediaContext | None = None,
                   max_file_chars: int = 4000, with_images: bool = True,
                   cache_file=None) -> list[Message]:
    """取某会话 sort_seq > after_seq 的消息。文本直接读，非文本走 media 解析。

    text_types：当作纯文本处理的 base 类型（默认 [1]）。其余 base 类型全部尝试解析成
    Attachment（图/文件/引用/转发/转账/语音元信息里能得到多少算多少）。
    """
    if session.table is None:
        return []
    senders = senders if senders is not None else _sender_map(work)
    displays = displays if displays is not None else _display_names(work)
    con = connect(work / "message" / "message_0.db")
    try:
        rows = con.execute(
            f"select local_id, sort_seq, real_sender_id, create_time, local_type, "
            f"message_content, WCDB_CT_message_content from {session.table} "
            f"where sort_seq > ? order by sort_seq limit ?",
            (after_seq, limit)).fetchall()
    finally:
        con.close()

    msgs: list[Message] = []
    for local_id, seq, sender_id, ts, local_type, content, _ct in rows:
        body = decompress(content)
        if not body:
            continue
        if session.is_chatroom:
            body = PREFIX_RE.sub("", body)
        body = body.strip(TRAILING_SPACE)
        if not body:
            continue

        sender = senders.get(sender_id, "")
        base, _sub = decode_type(local_type)
        atts: list[Attachment] = []
        text = ""
        if base in text_types:
            text = body
        elif media is not None:
            att = parse_attachment(body, local_type, media, max_file_chars=max_file_chars,
                                   with_images=with_images, cache_file=cache_file)
            if att is None:
                continue
            atts.append(att)
        else:
            continue

        msgs.append(Message(
            session=session.username,
            session_display=session.display,
            local_id=int(local_id),
            sort_seq=int(seq),
            ts=int(ts),
            sender=sender,
            sender_display=displays.get(sender, sender or "我"),
            text=text,
            attachments=atts,
        ))
    return msgs


def make_media_context(work: Path, account_root: Path | None,
                       image_tool_dir: Path | None) -> MediaContext | None:
    if account_root is None or not account_root.is_dir():
        return None
    return MediaContext(account_root=account_root,
                        hardlink=build_hardlink_index(work),
                        upstream_dir=image_tool_dir)
