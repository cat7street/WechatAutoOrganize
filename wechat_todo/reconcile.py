"""回溯核对：找出"之前抽成待办、但后续对话明确推翻了"的条目。

为什么需要单独一步：
增量抽取只处理水位线之后的新消息。一条待办的"诞生消息"被消费掉之后，
后面那句"算了不要了"再也不会被同一批处理看到，所以必须在**后续消息**里回头核对。

做法：按会话取出最近 N 条消息 + 该会话所有 pending 待办，让模型判断
"这些后续消息是否取消了其中某条"，只认这批消息里的原文作证据。
"""
from __future__ import annotations

import json

from .config import Config
from .llm import LLM, Usage
from .store import Store

SYSTEM = """你在判断：后续对话是否**明确推翻**了已有待办。输出会被程序直接 json.loads。

判定规则：
1. 只有明确的否定才算取消：“算了”“不用了”“先不做”“别弄了”“不搞了”“改主意了”
   “那个先放着”等；或给出了替代方案取代原计划。
2. 沉默、转移话题、“再看吧”“回头说”都**不算**取消。
3. 已经做完的（“搞定了”“弄完了”）既不算取消，也不要报。
4. 证据必须逐字来自给定消息，并且 msg_ids 必须是给定消息里的 id。

输出格式：
{"cancelled":[{"task_id":0,"reason":"取消依据原文（≤40字）","msg_ids":[1,2]}]}

没有取消就输出 {"cancelled":[]}。第一个字符必须是 {，禁止任何解释、markdown、代码围栏。"""


def _task_block(tasks: list[dict]) -> str:
    lines = ["待核对的待办："]
    for t in tasks:
        due = f" 截止{t['due']}" if t.get("due") else ""
        quote = f" 出自：“{t['quote'][:40]}”" if t.get("quote") else ""
        lines.append(f"  {t['id']}. {t['title']}{due}{quote}")
    return "\n".join(lines)


def reconcile(cfg: Config, store: Store, log=print, *, lookback: int = 150,
              max_sessions: int = 8) -> tuple[dict, Usage]:
    from . import view as viewer

    pending = [t for t in store.list_tasks(status="pending", limit=400)]
    stats = {"sessions": 0, "checked_tasks": 0, "cancelled": 0}
    usage = Usage()
    if not pending:
        return stats, usage

    by_session: dict[str, list[dict]] = {}
    for t in pending:
        by_session.setdefault(t["session"], []).append(t)

    llm = LLM(cfg)
    for username, tasks in list(by_session.items())[:max_sessions]:
        view = viewer.session_messages(cfg, username, limit=lookback, with_images=False)
        msgs = view.get("messages") or []
        if not msgs:
            continue
        stats["sessions"] += 1
        stats["checked_tasks"] += len(tasks)
        lines = [f"[{m['local_id']}] {m['sender_display']}: {m['text']}"
                 if m["text"] else f"[{m['local_id']}] {m['sender_display']}: "
                 f"{' '.join(a['label'] for a in m['attachments'])}"
                 for m in msgs]
        user = (f"会话：{view.get('display', username)}\n\n"
                + _task_block(tasks) + "\n\n"
                + "---- 这些待办之后的最新对话 ----\n" + "\n".join(lines)
                + "\n---- 结束 ----\n"
                  "现在只输出 JSON：判断这批对话取消了哪些待办。")
        log(f"[reconcile] {view.get('display', username)}：{len(tasks)} 条待办 vs {len(msgs)} 条后续消息")
        data, u = llm.complete_json(SYSTEM, user)
        usage.add(u)
        valid = {m["local_id"] for m in msgs}
        task_ids = {t["id"] for t in tasks}
        for raw in (data.get("cancelled") or []):
            try:
                tid = int(raw.get("task_id") or 0)
            except (TypeError, ValueError):
                continue
            ev = [int(x) for x in (raw.get("msg_ids") or []) if int(x) in valid]
            if tid not in task_ids or not ev:
                continue
            if store.cancel_task(tid, (raw.get("reason") or "").strip(), ev):
                stats["cancelled"] += 1
                log(f"[reconcile] 取消 #{tid}：《{(raw.get('reason') or '')[:30]}》")
    return stats, usage
