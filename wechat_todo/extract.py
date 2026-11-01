"""把聊天片段喂给模型，抽成结构化待办。分批只按字符预算切，不跨会话混装。"""
from __future__ import annotations

from .llm import LLM, Usage
from .schema import Message, Task

SYSTEM_PROMPT = """你是一个精准的待办抽取器。输入是微信群或单聊的聊天记录片段，每行格式：
[msgid] MM-DD HH:MM 发送人: 内容

只抽取真正可执行的待办。判定标准（满足其一才算）：
1. 有明确动作 + 明确责任人（我、某人或某方）；
2. 有明确时间点（今天/明天/周X/月底/具体日期/截止前）且带动作；
3. 有人明确提出请求、指派或承诺要做某事。

必须忽略：闲聊寒暄、表情与语气词、广告与刷屏营销、无责任人的泛泛感叹、
纯信息同步且无后续动作的内容。

只输出 JSON，结构：
{"tasks":[{"title":"","owner":"","due":"","priority":"high|medium|low",
           "quote":"","msg_ids":[],"confidence":0.0}]}

字段要求：
- title：动词开头的短句，不超过 30 字，写清"做什么"
- owner：责任人微信名；不明确就填空字符串
- due：ISO 日期 YYYY-MM-DD；相对时间按该消息的时间换算；没有就留空
- priority：high=有硬截止或影响他人 / medium=正常 / low=可选
- quote：支撑这条待办的原文片段，不超过 60 字，必须逐字来自聊天
- msg_ids：支撑它的消息 msgid 整数数组，至少一个
- confidence：0~1，你有多确定这真是一条待办

没有任何待办时输出 {"tasks":[]}。禁止编造，禁止输出 JSON 之外的任何文字。"""


def _pack(messages: list[Message], budget: int) -> list[list[Message]]:
    """按字符预算切批，保持时间顺序。"""
    batches: list[list[Message]] = []
    cur: list[Message] = []
    size = 0
    for m in messages:
        line = m.line()
        if cur and size + len(line) > budget:
            batches.append(cur)
            cur, size = [], 0
        cur.append(m)
        size += len(line) + 1
    if cur:
        batches.append(cur)
    return batches


def build_user_prompt(session_display: str, batch: list[Message]) -> str:
    head = (f"会话：{session_display}\n"
            f"消息条数：{len(batch)}\n"
            f"时间跨度：{batch[0].line()[:22]} 起\n"
            f"---- 聊天记录开始 ----")
    body = "\n".join(m.line() for m in batch)
    return f"{head}\n{body}\n---- 聊天记录结束 ----"


def extract_session(llm: LLM, session_display: str, messages: list[Message],
                    budget: int, session: str, log=print
                    ) -> tuple[list[Task], Usage]:
    tasks: list[Task] = []
    usage = Usage()
    batches = _pack(messages, budget)
    for i, batch in enumerate(batches, 1):
        log(f"[extract] {session_display} 批次 {i}/{len(batches)}（{len(batch)} 条）")
        data, u = llm.complete_json(SYSTEM_PROMPT, build_user_prompt(session_display, batch))
        usage.add(u)
        id_set = {m.local_id for m in batch}
        for raw in data.get("tasks", []):
            ids = [int(x) for x in raw.get("msg_ids", []) if int(x) in id_set]
            if not ids:
                # 没有可核对的原文消息就不收，避免模型幻觉进库
                continue
            title = (raw.get("title") or "").strip()
            if not title:
                continue
            tasks.append(Task(
                title=title,
                session=session,
                session_display=session_display,
                owner=(raw.get("owner") or "").strip(),
                due=(raw.get("due") or "").strip(),
                priority=raw.get("priority") if raw.get("priority") in
                ("high", "medium", "low") else "medium",
                quote=(raw.get("quote") or "").strip(),
                msg_ids=ids,
                confidence=float(raw.get("confidence") or 0.0),
            ))
    return tasks, usage
