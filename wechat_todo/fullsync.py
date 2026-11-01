"""全量对账：把一段时间窗的完整对话 + 当前待办清单一起交给模型，让它整体更新清单。

和"增量抽取"的区别：
- 增量抽取：只看水位线之后的新消息，只能"加"待办，看不到全局。
- 全量对账（本模块）：看 48h 完整对话 + 现有清单，一次给出全部差异：
  新增 / 改标题·截止·责任人·优先级 / 取消 / 被替代 / 恢复。

规则依然只做"能验真"的活：证据必须逐字出自窗口、位点必须真实存在、证据太短驳回。
"""
from __future__ import annotations

import json
import re

from .config import Config
from .llm import LLM, Usage
from .schema import Task
from .store import Store

PRIORITIES = {"high", "medium", "low"}

SYSTEM = """你在维护一份待办清单。输入 = 一段完整对话 + 当前待办清单。输出 = 需要做的**差异**。只输出 JSON。

要判断的事：
- new：对话里新出现、清单里没有的待办
- update：清单里某条要改（标题更准、截止改了、责任人明确了、优先级变了）
- cancel：明确不做了（“算了”“不用了”“先不做”“别搞了”“改主意了”）
- supersede：被新方案取代（“公开”→“改成私有”、“续费旧机器”→“换台新的下单了”）
- restore：之前判为取消、后来又要做了

判定方法：
1. 先按时间顺序读完整段对话，再对照清单逐条看结局。对话常常反复
   （先说换、后说换不了、最后还是续费）——以**最后一条决定性表态**为准。
2. 一律不要重复输出清单里已经存在、且不需要改动的条目。
3. 每条都要给 evidence_quote（必须是对话里某条消息的**逐字原文片段**，别只引一两个字）
   和 decided_at_msg_id（方括号里的数字）；拿不准就给低 confidence，不要硬报。
4. 截止时间按“今天日期”换算成 YYYY-MM-DD。图片/文件内容也是依据。

输出格式：
{"new":[{"title":"","owner":"","due":"","priority":"high|medium|low",
         "quote":"","msg_ids":[],"confidence":0.0}],
 "update":[{"task_id":0,"title":"","owner":"","due":"","priority":"",
            "evidence_quote":"","decided_at_msg_id":0,"confidence":0.0}],
 "cancel":[{"task_id":0,"evidence_quote":"","decided_at_msg_id":0,"confidence":0.0}],
 "supersede":[{"task_id":0,"replacement":"","evidence_quote":"","decided_at_msg_id":0,
               "confidence":0.0}],
 "restore":[{"task_id":0,"evidence_quote":"","decided_at_msg_id":0,"confidence":0.0}]}

没有差异就各项给空数组。第一个字符必须是 {，禁止任何解释、markdown、代码围栏。"""


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _task_lines(tasks: list[dict]) -> str:
    lines = ["当前待办清单："]
    for t in tasks:
        bits = [f"  {t['id']}. {t['title']}（{t['status']}" + (f"，{t['due']}" if t.get("due") else "")
                + (f"，{t['owner']}" if t.get("owner") else "")
                + f"，{t['priority']}）"]
        if t.get("quote"):
            bits.append(f"      原话：“{t['quote'][:50]}”")
        lines.append("\n".join(bits))
    return "\n".join(lines)


def _msg_lines(msgs: list[dict]) -> str:
    out = []
    for m in msgs:
        body = m["text"] or " ".join(a["label"] for a in m["attachments"])
        out.append(f"[{m['local_id']}] {m['sender_display']}: {body}")
    return "\n".join(out)


def _verify(raw: dict, ids: set[int], text_all: str) -> tuple[bool, str, str, int, float]:
    quote = (raw.get("evidence_quote") or raw.get("quote") or "").strip()
    try:
        decided = int(raw.get("decided_at_msg_id") or 0)
        conf = float(raw.get("confidence") or 0)
    except (TypeError, ValueError):
        return False, "字段类型非法", quote, 0, 0.0
    if len(_norm(quote)) < 4:
        return False, "证据太短", quote, decided, conf
    if _norm(quote) not in text_all:
        return False, "证据不是逐字原文", quote, decided, conf
    if decided and decided not in ids:
        return False, f"定位 msg{decided} 不在窗口内", quote, decided, conf
    return True, "", quote, decided, conf


def sync(cfg: Config, store: Store, log=print, *, hours: int | None = None,
         max_context_chars: int = 160_000, tasks_per_call: int = 12,
         max_calls: int = 10, session_filter: str | None = None) -> tuple[dict, Usage, int]:
    """返回 (统计, 用量, 窗口内最新 sort_seq)。"""
    from . import scan, view as viewer

    hours = hours or cfg.window_hours
    stats = {"sessions": 0, "calls": 0, "new": 0, "inserted": 0, "updated": 0,
             "cancelled": 0, "superseded": 0, "restored": 0, "rejected": 0}
    usage = Usage()
    newest_seq = 0

    sessions = [s for s in scan.list_sessions(cfg.work_dir, cfg.whitelist) if s.whitelisted]
    if session_filter:
        sessions = [s for s in sessions if session_filter in (s.username, s.display)]
    if not sessions:
        raise RuntimeError("白名单没有命中任何会话")

    llm = LLM(cfg)
    open_tasks = store.list_for_reconcile(limit=300)
    by_session: dict[str, list[dict]] = {}
    for t in open_tasks:
        by_session.setdefault(t["session"], []).append(t)

    for s in sessions:
        win = viewer.recent_window(cfg, s.username, hours=hours, max_chars=max_context_chars)
        msgs = win.get("messages") or []
        if not msgs:
            continue
        stats["sessions"] += 1
        newest_seq = max(newest_seq, max(m["sort_seq"] for m in msgs))
        ids = {m["local_id"] for m in msgs}
        text_all = _norm(" ".join(
            (m["text"] or "") + " ".join(a["label"] + (a.get("text") or "")
                                         for a in m["attachments"]) for m in msgs))
        tasks = by_session.get(s.username, [])
        header = (f"今天日期：{__import__('datetime').date.today().isoformat()}\n"
                  f"会话：{win.get('display', s.display)}\n"
                  f"时间窗：最近 {hours} 小时，{len(msgs)} 条消息"
                  + ("（超长已截断，保留最新）" if win.get("truncated") else "") + "\n\n")
        body = _msg_lines(msgs)

        chunks = [tasks[i:i + tasks_per_call] for i in range(0, len(tasks), tasks_per_call)] or [[]]
        for idx, group in enumerate(chunks):
            if stats["calls"] >= max_calls:
                break
            stats["calls"] += 1
            ask_new = idx == 0  # 新待办只让第一批报，避免重复
            user = (header + _task_lines(group)
                    + ("" if ask_new else "\n（本批只做更新/取消，不要报 new）")
                    + "\n\n---- 完整对话（按时间顺序）----\n" + body
                    + "\n---- 结束 ----\n只输出 JSON 差异。")
            log(f"[sync] {win.get('display', s.display)}：{len(group)} 条待办 vs {len(msgs)} 条对话")
            data, u = llm.complete_json(SYSTEM, user)
            usage.add(u)

            # --- 新增 ---
            if ask_new:
                batch_tasks: list[Task] = []
                for raw in data.get("new") or []:
                    ids_ev = [int(x) for x in (raw.get("msg_ids") or []) if int(x) in ids]
                    title = (raw.get("title") or "").strip()
                    if not ids_ev or not title:
                        continue
                    quote = (raw.get("quote") or "").strip()
                    if quote and _norm(quote) not in text_all:
                        stats["rejected"] += 1
                        continue
                    batch_tasks.append(Task(
                        title=title, session=s.username, session_display=s.display,
                        owner=(raw.get("owner") or "").strip(),
                        due=(raw.get("due") or "").strip(),
                        priority=raw.get("priority") if raw.get("priority") in PRIORITIES else "medium",
                        quote=quote, msg_ids=sorted(set(ids_ev)),
                        confidence=float(raw.get("confidence") or 0)))
                stats["new"] += len(batch_tasks)
                ins, _dup = store.save_tasks(batch_tasks, run_id=f"sync-{int(__import__('time').time())}")
                stats["inserted"] += ins

            # --- 更新字段 ---
            group_ids = {t["id"] for t in group}
            for raw in data.get("update") or []:
                try:
                    tid = int(raw.get("task_id") or 0)
                except (TypeError, ValueError):
                    continue
                if tid not in group_ids:
                    continue
                fields = {}
                if (raw.get("title") or "").strip():
                    fields["title"] = raw["title"].strip()
                if (raw.get("owner") or "").strip():
                    fields["owner"] = raw["owner"].strip()
                if (raw.get("due") or "").strip():
                    fields["due"] = raw["due"].strip()
                if raw.get("priority") in PRIORITIES:
                    fields["priority"] = raw["priority"]
                if not fields:
                    continue
                store.update_task(tid, **fields)
                stats["updated"] += 1

            # --- 取消 / 替代 / 恢复 ---
            for key, target in (("cancel", "cancelled"), ("supersede", "superseded")):
                for raw in data.get(key) or []:
                    try:
                        tid = int(raw.get("task_id") or 0)
                    except (TypeError, ValueError):
                        continue
                    task = next((t for t in group if t["id"] == tid), None)
                    if task is None:
                        continue
                    ok, why, quote, decided, conf = _verify(raw, ids, text_all)
                    if not ok:
                        stats["rejected"] += 1
                        log(f"[sync] 驳回 {key} #{tid}：{why}")
                        continue
                    if conf < 0.6:
                        store.annotate(tid, f"疑似{target}（msg{decided}：{quote[:40]}）")
                        continue
                    note = (f"被替代：{(raw.get('replacement') or '').strip()}"
                            if key == "supersede" and raw.get("replacement") else "")
                    if store.cancel_task(tid, quote, [decided], note):
                        stats["superseded" if key == "supersede" else "cancelled"] += 1
                        log(f"[sync] #{tid} → {target}（msg{decided}：{quote[:26]}）")

            for raw in data.get("restore") or []:
                try:
                    tid = int(raw.get("task_id") or 0)
                except (TypeError, ValueError):
                    continue
                task = next((t for t in group if t["id"] == tid), None)
                if task is None or task["status"] != "cancelled":
                    continue
                ok, why, quote, decided, _conf = _verify(raw, ids, text_all)
                if not ok:
                    stats["rejected"] += 1
                    log(f"[sync] 驳回 restore #{tid}：{why}")
                    continue
                if store.restore_task(tid, f"又要做了（msg{decided}：{quote[:30]}）"):
                    stats["restored"] += 1
                    log(f"[sync] #{tid} 恢复为待办")

            for t in group:
                store.mark_reconciled(t["id"], max(m["sort_seq"] for m in msgs))
    return stats, usage, newest_seq
