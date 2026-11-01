"""会话内容查看：给面板提供"这条待办到底是从哪几句话来的"。

图片默认不解码（浏览场景只给占位），需要时用 with_images=True。
"""
from __future__ import annotations

from . import scan
from .config import Config
from .media import decode_type, parse_attachment


def _session(cfg: Config, username: str):
    for s in scan.list_sessions(cfg.work_dir, []):
        if s.username == username:
            return s
    return None


def _rows(cfg: Config, username: str, *, before_seq: int | None, after_seq: int | None,
          limit: int):
    """按 sort_seq 取一段消息（降序取最近 limit 条再翻回时间顺序）。"""
    session = _session(cfg, username)
    if session is None or session.table is None:
        return session, []
    con = scan.connect(cfg.work_dir / "message" / "message_0.db")
    try:
        where = []
        args: list = []
        if before_seq is not None:
            where.append("sort_seq < ?")
            args.append(before_seq)
        if after_seq is not None:
            where.append("sort_seq > ?")
            args.append(after_seq)
        clause = (" where " + " and ".join(where)) if where else ""
        sql = (f"select local_id, sort_seq, real_sender_id, create_time, local_type, "
               f"message_content from {session.table}{clause} "
               f"order by sort_seq desc limit ?")
        args.append(limit)
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    return session, list(reversed(rows))


def _fmt(session, rows, displays: dict[str, str], senders: dict[int, str],
         media_ctx, cfg: Config, with_images: bool) -> list[dict]:
    out: list[dict] = []
    for local_id, seq, sender_id, ts, local_type, content in rows:
        body = scan.decompress(content)
        if not body:
            continue
        text = body
        base, sub = decode_type(local_type)
        atts: list[dict] = []
        if base in cfg.text_types:
            if session.is_chatroom:
                text = scan.PREFIX_RE.sub("", text)
            text = text.strip(scan.TRAILING_SPACE)
        else:
            text = ""
            att = (parse_attachment(body, local_type, media_ctx, max_file_chars=400,
                                    with_images=with_images, cache_file=cfg.media_cache)
                   if media_ctx is not None else None)
            if att is not None:
                atts.append({"kind": att.kind, "label": att.label,
                             "text": att.text, "has_image": bool(att.image_b64)})
        sender = senders.get(sender_id, "")
        out.append({
            "local_id": int(local_id),
            "sort_seq": int(seq),
            "ts": int(ts),
            "sender": sender,
            "sender_display": displays.get(sender, sender or "我"),
            "type": f"{base}/{sub}" if sub else str(base),
            "text": text,
            "attachments": atts,
        })
    return out


def session_messages(cfg: Config, username: str, *, before_seq: int | None = None,
                     limit: int = 100, with_images: bool = False) -> dict:
    from . import upstream
    media_ctx = scan.make_media_context(cfg.work_dir, cfg.account_root(),
                                        upstream.prepare(cfg))
    session, rows = _rows(cfg, username, before_seq=before_seq, after_seq=None, limit=limit)
    if session is None:
        return {"session": username, "display": username, "messages": [], "has_more": False}
    displays, senders = scan._display_names(cfg.work_dir), scan._sender_map(cfg.work_dir)
    msgs = _fmt(session, rows, displays, senders, media_ctx, cfg, with_images)
    return {
        "session": session.username,
        "display": session.display,
        "messages": msgs,
        "has_more": len(rows) >= limit,
        "next_before_seq": rows[0][1] if rows else None,
    }


def task_context(cfg: Config, task: dict, *, window: int = 5,
                 with_images: bool = False) -> dict:
    """取这条待办引用消息的前后 window 条，用来核对原文。"""
    from . import upstream
    media_ctx = scan.make_media_context(cfg.work_dir, cfg.account_root(),
                                        upstream.prepare(cfg))
    ids = task.get("msg_ids") or []
    session_user = task["session"]
    session = _session(cfg, session_user)
    if session is None:
        return {"task_id": task["id"], "messages": [], "missing": "会话不存在"}
    con = scan.connect(cfg.work_dir / "message" / "message_0.db")
    try:
        seqs = []
        for mid in ids:
            row = con.execute(
                f"select sort_seq from {session.table} where local_id=?", (mid,)).fetchone()
            if row:
                seqs.append(int(row[0]))
    finally:
        con.close()
    if not seqs:
        return {"task_id": task["id"], "messages": [], "missing": "原文消息已不在库中"}
    lo, hi = min(seqs), max(seqs)
    displays, senders = scan._display_names(cfg.work_dir), scan._sender_map(cfg.work_dir)
    _, rows = _rows(cfg, session_user, before_seq=hi + 1, after_seq=lo - 1,
                    limit=len(ids) + window * 2 + 10)
    msgs = _fmt(session, rows, displays, senders, media_ctx, cfg, with_images)
    for m in msgs:
        m["is_source"] = m["local_id"] in ids
    return {"task_id": task["id"], "session": session.username, "display": session.display,
            "messages": msgs}
