"""待办语义合并：把"同一件事的不同说法"聚成一条，并按反复提及次数抬优先级。

分工：
- 语义判断交给模型（哪些条是同一件事、合并后怎么叫）
- 提优先级有确定性规则兜底不了的部分：mentions 达到阈值就必须是某档，模型降不下来
这两件事分开做，是为了让优先级可解释、可复算。
"""
from __future__ import annotations

import json

from .config import Config
from .llm import LLM, Usage
from .store import Store

PRIO_ORDER = {"low": 0, "medium": 1, "high": 2}


def floor_by_mentions(mentions: int) -> str:
    """反复提及次数决定优先级下限。"""
    if mentions >= 5:
        return "high"
    if mentions >= 2:
        return "medium"
    return "low"


def lift_priority(current: str, mentions: int) -> str:
    floor = floor_by_mentions(mentions)
    cur = current if current in PRIO_ORDER else "medium"
    return cur if PRIO_ORDER[cur] >= PRIO_ORDER[floor] else floor


SYSTEM = """你在做待办去重聚簇，不是在聊天。输出会被程序直接 json.loads。

输入是一批从微信聊天里抽出的待办（JSON 数组，每项有 id/title/session/owner/due）。
任务：把**指向同一件事**的条目合并成一组。

判定"同一件事"的标准：
- 动作和目标相同，只是措辞不同 → 合并（例："续费服务器" / "给服务器办理续费"）
- 同一项目的不同动作（"部署 A" 与 "给 A 配 SSL"）→ 不合并
- 只是同一话题/同一对象但动作不同 → 不合并

输出格式：
{"groups":[{"ids":[1,2],"title":"合并后标题（动词开头，≤30字）",
            "owner":"","due":"","priority":"high|medium|low","reason":"为何同一件事"}],
 "keep":[3,4]}

规则：
1. 每组至少 2 个 id；只有一组的成员不能被两个组同时引用。
2. 没被合并的 id 一律放进 keep。
3. priority 按"这件事有多重要多紧急"给，不要照抄原条目。
4. 第一个字符必须是 {，禁止任何解释、标题、markdown、代码围栏。"""


def merge_pending(cfg: Config, store: Store, log=print,
                  limit: int = 150) -> tuple[dict, Usage]:
    rows = store.list_pending_for_merge(limit)
    stats = {"candidates": len(rows), "groups": 0, "merged_away": 0, "lifted": 0}
    usage = Usage()
    if len(rows) < 2:
        return stats, usage

    payload = [{"id": r["id"], "title": r["title"], "session": r["session_display"],
                "owner": r["owner"], "due": r["due"], "mentions": len(r["msg_ids"])}
               for r in rows]
    by_id = {r["id"]: r for r in rows}

    log(f"[merge] 候选 {len(rows)} 条，交给模型聚簇")
    data, usage = LLM(cfg).complete_json(
        SYSTEM,
        "现在对这些待办聚簇，只输出 JSON：\n" + json.dumps(payload, ensure_ascii=False))
    used: set[int] = set()
    for group in data.get("groups", []):
        ids = [int(i) for i in group.get("ids", []) if int(i) in by_id and int(i) not in used]
        if len(ids) < 2:
            continue
        ids.sort(key=lambda i: by_id[i]["created_at"])  # 最早那条当 canonical
        canonical, members = ids[0], ids[1:]
        union_msgs: set[int] = set()
        for i in ids:
            union_msgs.update(by_id[i]["msg_ids"])
        mentions = len(union_msgs)
        title = (group.get("title") or by_id[canonical]["title"]).strip()
        owner = (group.get("owner") or by_id[canonical]["owner"]).strip()
        due = (group.get("due") or by_id[canonical]["due"]).strip()
        model_prio = group.get("priority") if group.get("priority") in PRIO_ORDER else "medium"
        final_prio = lift_priority(model_prio, mentions)
        if PRIO_ORDER[final_prio] > PRIO_ORDER[model_prio]:
            stats["lifted"] += 1
        store.apply_merge(canonical, members, title=title, owner=owner, due=due,
                          priority=final_prio)
        used.update(ids)
        stats["groups"] += 1
        stats["merged_away"] += len(members)
        log(f"[merge] 合并 {len(ids)} 条 → 《{title}》 提及 {mentions} 次 → {final_prio}"
            f"（{group.get('reason', '')[:40]}）")

    # 没进任何簇的条目：mentions 用自身消息数刷一遍，并套用同一套下限规则
    for r in rows:
        if r["id"] in used:
            continue
        mentions = len(r["msg_ids"]) or 1
        final_prio = lift_priority(r["priority"], mentions)
        if final_prio != r["priority"]:
            store.apply_merge(r["id"], [], title=r["title"], owner=r["owner"],
                              due=r["due"], priority=final_prio)
            stats["lifted"] += 1

    return stats, usage
