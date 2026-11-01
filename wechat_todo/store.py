"""状态库：水位线游标、待办表、运行记录与用量记账。"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from .schema import Task

DDL = """
create table if not exists cursor(
    session text primary key,
    last_sort_seq integer not null,
    updated_at integer not null
);
create table if not exists tasks(
    id integer primary key autoincrement,
    hash text not null unique,
    session text not null,
    session_display text not null,
    title text not null,
    owner text default '',
    due text default '',
    priority text default 'medium',
    quote text default '',
    msg_ids text default '[]',
    confidence real default 0,
    status text not null default 'pending',
    created_at integer not null,
    run_id text
);
create table if not exists runs(
    id text primary key,
    started_at integer not null,
    finished_at integer,
    ok integer,
    stats text default '{}',
    prompt_tokens integer default 0,
    completion_tokens integer default 0
);
create index if not exists idx_tasks_status on tasks(status, created_at desc);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(DDL)
        self.con.commit()

    # ---------- 水位线 ----------
    def get_cursor(self, session: str) -> int:
        row = self.con.execute(
            "select last_sort_seq from cursor where session=?", (session,)).fetchone()
        return int(row["last_sort_seq"]) if row else 0

    def set_cursor(self, session: str, seq: int) -> None:
        self.con.execute(
            "insert into cursor(session,last_sort_seq,updated_at) values(?,?,?) "
            "on conflict(session) do update set last_sort_seq=excluded.last_sort_seq,"
            "updated_at=excluded.updated_at",
            (session, seq, int(time.time())))
        self.con.commit()

    # ---------- 待办 ----------
    def save_tasks(self, tasks: list[Task], run_id: str) -> tuple[int, int]:
        inserted = skipped = 0
        for t in tasks:
            h = t.dedupe_hash()
            exists = self.con.execute("select 1 from tasks where hash=?", (h,)).fetchone()
            if exists:
                skipped += 1
                continue
            self.con.execute(
                "insert into tasks(hash,session,session_display,title,owner,due,priority,"
                "quote,msg_ids,confidence,status,created_at,run_id) "
                "values(?,?,?,?,?,?,?,?,?,?,'pending',?,?)",
                (h, t.session, t.session_display, t.title, t.owner, t.due, t.priority,
                 t.quote, json.dumps(t.msg_ids), t.confidence, int(time.time()), run_id))
            inserted += 1
        self.con.commit()
        return inserted, skipped

    def list_tasks(self, status: str | None = None, limit: int = 500) -> list[dict]:
        sql = "select * from tasks"
        args: list = []
        if status:
            sql += " where status=?"
            args.append(status)
        sql += " order by created_at desc limit ?"
        args.append(limit)
        out = []
        for r in self.con.execute(sql, args):
            d = dict(r)
            d["msg_ids"] = json.loads(d["msg_ids"] or "[]")
            out.append(d)
        return out

    def tasks_of_run(self, run_id: str) -> list[dict]:
        out = []
        for r in self.con.execute(
                "select * from tasks where run_id=? order by created_at desc", (run_id,)):
            d = dict(r)
            d["msg_ids"] = json.loads(d["msg_ids"] or "[]")
            out.append(d)
        return out

    def set_task_status(self, task_id: int, status: str) -> bool:
        cur = self.con.execute("update tasks set status=? where id=?", (status, task_id))
        self.con.commit()
        return cur.rowcount > 0

    def update_task(self, task_id: int, **fields) -> bool:
        allowed = {"title", "owner", "due", "priority", "quote", "status"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return False
        clause = ",".join(f"{k}=?" for k in sets)
        cur = self.con.execute(f"update tasks set {clause} where id=?",
                               (*sets.values(), task_id))
        self.con.commit()
        return cur.rowcount > 0

    # ---------- 运行记录 ----------
    def start_run(self) -> str:
        rid = uuid.uuid4().hex[:12]
        self.con.execute("insert into runs(id,started_at) values(?,?)",
                         (rid, int(time.time())))
        self.con.commit()
        return rid

    def finish_run(self, run_id: str, ok: bool, stats: dict,
                   prompt_tokens: int, completion_tokens: int) -> None:
        self.con.execute(
            "update runs set finished_at=?,ok=?,stats=?,prompt_tokens=?,completion_tokens=? "
            "where id=?",
            (int(time.time()), 1 if ok else 0, json.dumps(stats, ensure_ascii=False),
             prompt_tokens, completion_tokens, run_id))
        self.con.commit()

    def list_runs(self, limit: int = 20) -> list[dict]:
        out = []
        for r in self.con.execute(
                "select * from runs order by started_at desc limit ?", (limit,)):
            d = dict(r)
            d["stats"] = json.loads(d["stats"] or "{}")
            out.append(d)
        return out

    def stats(self) -> dict:
        c = self.con
        by_status = {r["status"]: r["n"] for r in
                     c.execute("select status, count(*) n from tasks group by status")}
        by_priority = {r["priority"]: r["n"] for r in
                       c.execute("select priority, count(*) n from tasks group by priority")}
        total_tasks = c.execute("select count(*) n from tasks").fetchone()["n"]
        usage = c.execute(
            "select coalesce(sum(prompt_tokens),0) pt, "
            "coalesce(sum(completion_tokens),0) ct from runs").fetchone()
        per_session = [dict(r) for r in c.execute(
            "select session_display, count(*) n from tasks group by session_display "
            "order by n desc limit 10")]
        recent = [{"date": r["d"], "n": r["n"]} for r in c.execute(
            "select date(created_at,'unixepoch','localtime') d, count(*) n "
            "from tasks group by d order by d desc limit 14")]
        return {
            "by_status": by_status,
            "by_priority": by_priority,
            "total_tasks": total_tasks,
            "prompt_tokens": usage["pt"],
            "completion_tokens": usage["ct"],
            "per_session": per_session,
            "recent_daily": list(reversed(recent)),
        }
