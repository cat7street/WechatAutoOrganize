"""回溯核对：找出"之前抽成待办、但后续对话明确推翻了"的条目。

为什么需要单独一步：
增量抽取只处理水位线之后的新消息。一条待办的"诞生消息"被消费掉之后，
后面那句"算了不要了"，或者"改成私有吧"这种替代方案，再也不会被同一批处理看到。

窗口怎么定（踩过的坑）：
一开始按"该会话最近 N 条"取，结果否定原话在更早的位置，被窗口切掉了 —— 于是判 0。
现在改成**按每条待办自己的证据位置往后取**：把会话内待办按证据消息时间排序，
每 group_size 条一组，取这组里最早那条之后的最新 window 条消息去核对。
"""
from __future__ import annotations

from .cancel_guard import accept as guard_accept
from .config import Config
from .llm import LLM, Usage
from .store import Store

SYSTEM = """你在判断：后续对话是否**明确推翻**了已有待办。输出会被程序直接 json.loads。

判定规则：
1. 明确的否定算取消：“算了”“不用了”“先不做”“别弄了”“不搞了”“改主意了”
   “那个先放着”“还是别搞”等。
2. **方案被替代也算取消**：后续提出了取代原做法的新做法，例如
   “公开” → “改成私有”、“放 A 服务器” → “放 B 服务器”、“用网页” → “改成客户端”、
   “要实时同步” → “不用实时同步”。原做法那条判取消（新做法会另外抽成新待办）。
3. 沉默、转移话题、“再看吧”“回头说”**不算**取消。
4. 已经做完的（“搞定了”“弄完了”）也不算取消，不要报。
5. 证据必须逐字来自给定消息，并且 msg_ids 必须是给定消息里的 id。
6. 拿不准就不要报 —— 宁可漏，不要误杀。

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


def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def reconcile(cfg: Config, store: Store, log=print, *, window: int = 40,
              group_size: int = 5, max_calls: int = 12) -> tuple[dict, Usage]:
    from . import view as viewer

    pending = store.list_tasks(status="pending", limit=400)
    stats = {"sessions": 0, "checked_tasks": 0, "cancelled": 0, "calls": 0}
    usage = Usage()
    if not pending:
        return stats, usage

    by_session: dict[str, list[dict]] = {}
    for t in pending:
        by_session.setdefault(t["session"], []).append(t)

    llm = LLM(cfg)
    for username, tasks in by_session.items():
        if stats["calls"] >= max_calls:
            break
        marks = viewer.seq_of(cfg, username, [i for t in tasks for i in t["msg_ids"]])
        positioned: dict[int, int] = {}
        for t in tasks:
            seq = max((marks.get(int(i), 0) for i in t["msg_ids"]), default=0)
            if seq:
                positioned[t["id"]] = seq
        if not positioned:
            continue
        stats["sessions"] += 1
        by_id = {t["id"]: t for t in tasks}

        for chunk in _chunks(sorted(positioned, key=lambda tid: positioned[tid]), group_size):
            if stats["calls"] >= max_calls:
                break
            lo = positioned[chunk[0]]
            view = viewer.messages_slice(cfg, username, lo,
                                        head=window, tail=window)
            msgs = view.get("messages") or []
            if not msgs:
                continue
            group = [by_id[tid] for tid in chunk]
            stats["checked_tasks"] += len(group)
            stats["calls"] += 1
            lines = [
                f"[{m['local_id']}] {m['sender_display']}: {m['text']}"
                if m["text"] else
                f"[{m['local_id']}] {m['sender_display']}: "
                f"{' '.join(a['label'] for a in m['attachments'])}"
                for m in msgs
            ]
            user = (f"会话：{view.get('display', username)}\n\n"
                    + _task_block(group) + "\n\n"
                    + "---- 这些待办之后的最新对话 ----\n" + "\n".join(lines)
                    + "\n---- 结束 ----\n"
                      "现在只输出 JSON：判断这批对话取消了哪些待办。")
            log(f"[reconcile] {view.get('display', username)}：{len(group)} 条待办 vs "
                f"{len(msgs)} 条后续消息（从 msg:{msgs[0]['local_id']} 起）")
            data, u = llm.complete_json(SYSTEM, user)
            usage.add(u)
            valid = {m["local_id"] for m in msgs}
            text_by_id = {m["local_id"]: (m["text"] or " ".join(
                a["label"] + a.get("text", "") for a in m["attachments"])) for m in msgs}
            task_ids = set(chunk)
            for raw in (data.get("cancelled") or []):
                try:
                    tid = int(raw.get("task_id") or 0)
                except (TypeError, ValueError):
                    continue
                ev = [int(x) for x in (raw.get("msg_ids") or []) if int(x) in valid]
                if tid not in task_ids or not ev:
                    continue
                ev_text = " ".join(text_by_id.get(i, "") for i in ev)
                ok, why = guard_accept(ev_text)
                if not ok:
                    log(f"[reconcile] 驳回了对 #{tid} 的取消：{why}")
                    continue
                if store.cancel_task(tid, (raw.get("reason") or "").strip(), ev):
                    stats["cancelled"] += 1
                    log(f"[reconcile] 取消 #{tid}：《{(raw.get('reason') or '')[:30]}》")
    return stats, usage
