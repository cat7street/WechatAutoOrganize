"""回溯核对：把整段时间窗（默认 48 小时）的完整对话交给模型，让它自己逐条判断。

为什么不给"每条待办一个小窗口"：
那等于我先替模型筛了证据——"换服务器"的结论往往在离待办很远的地方才有结果。
现在一次给整窗全量对话 + 一批待办，模型自己按时间线挨个判。

规则只做机器能验的事：
- decided_at_msg_id 必须真实存在于窗口
- evidence_quote 必须逐字出自窗口
- 终态非法直接驳回
- 同一待办多次核对，以最新一次为准（窗口右移）
"""
from __future__ import annotations

import json
import re

from .config import Config
from .llm import LLM, Usage
from .store import Store

STATES = {"pending", "cancelled", "superseded"}

SYSTEM = """你在核对一批待办的**最终状态**。只输出 JSON，会被程序直接 json.loads。

三种终态：
- pending     ：还要做（没被推翻；或之前说取消、后来又要做）
- cancelled   ：明确不做了（“算了”“不用了”“先不做”“别搞了”“改主意了”）
- superseded  ：被新方案取代（“公开”→“改成私有”、“续费旧机器”→“换台新的下单了”、
                “要实时同步”→“不用实时同步”）。原做法不再需要。

判定方法：
1. 按时间顺序读完整段对话，**自己找**每条待办的结局。不要只看待办附近几句——
   结论常常在聊了很久之后才出现；对话也常常反复（先说换、后说换不了、最后还是续费），
   以**最后一条决定性表态**为准。
2. 特别注意这种情况：先决定换方案，**后来又说换不了/做不成/还是算了吧** ——
   那原待办仍然要做，判 pending，decided_at_msg_id 用后面那条。
3. 每条待办尽量给结论；确实看不出来的给 pending + confidence<0.6。
4. evidence_quote 必须是对话里某条消息的**逐字原文片段**（不能改写），且真正支撑你的
   判定；只引“换”“行”这种一两个字的片段会被驳回，要引能说明问题的完整短句。
5. decided_at_msg_id 必须是那条消息的 id（方括号里的数字）。
6. replacement 只在 superseded 时写清“新方案是什么”。

输出格式：
{"decisions":[{"task_id":0,
  "final_state":"pending|cancelled|superseded",
  "replacement":"",
  "evidence_quote":"逐字原文",
  "decided_at_msg_id":0,
  "confidence":0.0}]}

每条待办都要有一条 decision，不要漏。第一个字符必须是 {，禁止任何解释、markdown、代码围栏。"""


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _task_lines(tasks: list[dict]) -> str:
    out = ["要核对的待办（逐条判断，用 id 引用；★备注是它自己的原始依据 msgid）："]
    for t in tasks:
        due = f" 截止{t['due']}" if t.get("due") else ""
        ids = ",".join(str(i) for i in t["msg_ids"][:6])
        out.append(f"  {t['id']}. {t['title']}{due}（当前:{t['status']}；依据 msg:{ids}）")
        if t.get("quote"):
            out.append(f"      原话：“{t['quote'][:50]}”")
    return "\n".join(out)


def reconcile(cfg: Config, store: Store, log=print, *, window_hours: int | None = None,
              max_context_chars: int = 160_000, tasks_per_call: int = 12,
              max_calls: int = 8) -> tuple[dict, Usage]:
    from . import view as viewer

    hours = window_hours or cfg.window_hours
    rows = store.list_for_reconcile(limit=200)
    stats = {"sessions": 0, "tasks": 0, "calls": 0, "cancelled": 0, "superseded": 0,
             "restored": 0, "suspected": 0, "rejected": 0, "truncated": 0}
    usage = Usage()
    if not rows:
        return stats, usage

    by_session: dict[str, list[dict]] = {}
    for t in rows:
        by_session.setdefault(t["session"], []).append(t)

    llm = LLM(cfg)
    for username, tasks in by_session.items():
        win = viewer.recent_window(cfg, username, hours=hours,
                                   max_chars=max_context_chars)
        msgs = win.get("messages") or []
        if not msgs:
            continue
        stats["sessions"] += 1
        stats["truncated"] += 1 if win.get("truncated") else 0
        ids = {m["local_id"] for m in msgs}
        text_all = _norm(" ".join(
            (m["text"] or "") + " ".join(a["label"] + (a.get("text") or "")
                                         for a in m["attachments"]) for m in msgs))
        header = (f"会话：{win.get('display', username)}\n"
                  f"时间窗：最近 {hours} 小时，共 {len(msgs)} 条消息"
                  + ("（超长已截断，保留最近部分）" if win.get("truncated") else "")
                  + "\n\n")
        body = "\n".join(
            f"[{m['local_id']}] {m['sender_display']}: "
            f"{m['text'] or ' '.join(a['label'] for a in m['attachments'])}"
            for m in msgs)

        for i in range(0, len(tasks), tasks_per_call):
            if stats["calls"] >= max_calls:
                return stats, usage
            group = tasks[i:i + tasks_per_call]
            stats["calls"] += 1
            stats["tasks"] += len(group)
            user = (header + _task_lines(group)
                    + "\n\n---- 完整对话（按时间顺序）----\n" + body
                    + "\n---- 结束 ----\n只输出 JSON：逐条给 decisions。")
            log(f"[reconcile] {win.get('display', username)}：{len(group)} 条待办 vs "
                f"{len(msgs)} 条对话（{hours}h）")
            data, u = llm.complete_json(SYSTEM, user)
            usage.add(u)
            decisions = data.get("decisions") or data.get("cancelled") or []
            by_id = {t["id"]: t for t in group}
            for raw in decisions:
                try:
                    tid = int(raw.get("task_id") or 0)
                except (TypeError, ValueError):
                    continue
                task = by_id.get(tid)
                if task is None:
                    continue
                state = str(raw.get("final_state") or "").strip()
                quote = (raw.get("evidence_quote") or "").strip()
                try:
                    decided = int(raw.get("decided_at_msg_id") or 0)
                    conf = float(raw.get("confidence") or 0)
                except (TypeError, ValueError):
                    decided, conf = 0, 0.0
                if state not in STATES:
                    stats["rejected"] += 1
                    log(f"[reconcile] 驳回 #{tid}：终态非法 {state!r}")
                    continue
                if state != "pending":
                    if decided not in ids:
                        stats["rejected"] += 1
                        log(f"[reconcile] 驳回 #{tid}：msg{decided} 不在窗口内")
                        continue
                    if len(_norm(quote)) < 4:
                        stats["rejected"] += 1
                        log(f"[reconcile] 驳回 #{tid}：证据太短 {quote!r}")
                        continue
                    if not quote or _norm(quote) not in text_all:
                        stats["rejected"] += 1
                        log(f"[reconcile] 驳回 #{tid}：证据非逐字原文 {quote[:26]!r}")
                        continue
                cur = task["status"]
                if state == "pending":
                    if cur != "pending" or task.get("cancelled_reason") or task.get("state_note"):
                        store.clear_cancel(task["id"],
                                           f"核对后确认仍要做（msg{decided}）" if decided else "")
                    if cur == "cancelled":
                        note = (f"曾被取消（msg:{','.join(map(str, task['cancelled_msg_ids']))}"
                                f"：{(task['cancelled_reason'] or '')[:30]}），后又要求做")
                        if store.restore_task(task["id"], note):
                            stats["restored"] += 1
                            log(f"[reconcile] #{tid} 恢复为待办")
                    continue
                if cur == state:
                    continue
                if conf < 0.6:
                    stats["suspected"] += 1
                    store.annotate(task["id"], f"疑似{state}（msg{decided}：{quote[:40]}）")
                    continue
                note = (f"被替代：{(raw.get('replacement') or '').strip()}"
                        if state == "superseded" and raw.get("replacement") else "")
                if store.cancel_task(task["id"], quote, [decided], note):
                    stats["superseded" if state == "superseded" else "cancelled"] += 1
                    log(f"[reconcile] #{tid} → {state}（msg{decided}：{quote[:30]}）")
            for t in group:
                store.mark_reconciled(t["id"], max(m["sort_seq"] for m in msgs))
    return stats, usage
