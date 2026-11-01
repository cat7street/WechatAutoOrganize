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

**取消与推翻（很重要）**：聊天里常见“先说要做、后面又不要了”。判定规则：
- 先提要做、随后明确否定（“算了”“不用了”“先不做”“改主意了”“别弄了”
  “不搞了”“先放着”）：**不要**抽成待办；
- **方案被替代也算取消**：后来说的做法把前面的做法替掉了（“公开”→“改私有”、
  “放 A 服务器”→“放 B 服务器”、“用网页”→“改成小程序”），只抽新方案，旧的不抽；
- 若被取消/被替代的正是「现有待办清单」里的某条，把它放进 cancelled，并给依据；
- 只认明确表态。沉默、转移话题、“再看吧”都算没取消。

非文本消息会以方括号标出：[图片] / [文件 xxx] / [引用 xxx] / [合并转发 xxx] /
[转账] / [语音 N 秒] / [视频 N 秒]。规则：
- 图片会作为图像一并提供。图里的聊天截图、通知、体检单、日程、报团等都要读，
  图里出现的时间地点金额就是待办依据；
- [文件 xxx] 后面跟的是从文件里抽出的正文，直接当内容读；
- [引用] 里的原文属于上下文，真待办以引用后的那句为准；
- [语音]/[视频] 只有时长元信息，不据此编造内容。

只输出 JSON，结构：
{"tasks":[{"title":"","owner":"","due":"","priority":"high|medium|low",
           "quote":"","msg_ids":[],"confidence":0.0}],
 "cancelled":[{"task_id":0,"reason":"","msg_ids":[]}]}

字段要求：
- title：动词开头的短句，不超过 30 字，写清"做什么"
- owner：责任人微信名；不明确就填空字符串
- due：ISO 日期 YYYY-MM-DD；相对时间按该消息的时间换算；没有就留空
- priority：high=有硬截止或影响他人 / medium=正常 / low=可选
- quote：支撑这条待办的原文片段，不超过 60 字，必须逐字来自聊天
- msg_ids：支撑它的消息 msgid 整数数组，至少一个
- confidence：0~1，你有多确定这真是一条待办

cancelled 字段：
- task_id：要取消的「现有待办清单」里的 id（不在清单里的不要填）
- reason：取消依据的原文说法，不超过 40 字
- msg_ids：依据消息的 msgid，至少一个

没有任何待办时输出 {"tasks":[],"cancelled":[]}。禁止编造，禁止输出 JSON 之外的任何文字。"""


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


def _pending_block(pending: list[dict] | None) -> str:
    """现有待办清单：让模型能发现“这段对话把哪条取消了”。"""
    if not pending:
        return "现有待办清单（空）：\n"
    lines = ["现有待办清单（本段对话可以取消其中的条目，用 id 引用）："]
    for t in pending:
        due = f" 截止{t['due']}" if t.get("due") else ""
        lines.append(f"  {t['id']}. {t['title']}{due}")
    return "\n".join(lines) + "\n"


def build_user_prompt(session_display: str, batch: list[Message],
                      pending: list[dict] | None = None) -> str:
    import datetime as _dt
    today = _dt.date.today().isoformat()
    head = (f"今天日期：{today}（相对时间按它换算，due 用 YYYY-MM-DD）\n"
            f"会话：{session_display}\n"
            f"消息条数：{len(batch)}\n"
            f"时间跨度：{batch[0].line()[:22]} 起\n"
            + _pending_block(pending)
            + "---- 聊天记录开始 ----")
    body = "\n".join(m.line() for m in batch)
    # 末尾必须再喊一次：实测该网关下模型会把“抽任务”当成聊天，格式约束只在开头写不管用
    tail = ('---- 聊天记录结束 ----\n'
            '现在只输出 JSON，第一个字符必须是 {，不要任何解释、标题、前言或 markdown：\n'
            '{"tasks":[{"title":"","owner":"","due":"","priority":"high|medium|low",'
            '"quote":"","msg_ids":[],"confidence":0}],'
            '"cancelled":[{"task_id":0,"reason":"","msg_ids":[]}]}')
    return f"{head}\n{body}\n{tail}"


def build_content(session_display: str, batch: list[Message],
                  max_images: int = 4, pending: list[dict] | None = None
                  ) -> str | list[dict]:
    """组装模型输入：纯文本，或带图片的多模态 content parts。

    图片按 msgid 顺序附在文本后面，并在文本里标注它来自哪条消息，保证 msg_ids 能回指。
    """
    text = build_user_prompt(session_display, batch, pending)
    images: list[tuple[int, str]] = []
    for m in batch:
        for a in m.attachments:
            if a.image_b64:
                images.append((m.local_id, a.image_b64))
    if not images:
        return text
    used = images[:max_images]
    note = "\n\n附带的图片依次来自这些消息（顺序与下方图片一致）：" + ", ".join(
        f"msg:{i}" for i, _ in used)
    parts: list[dict] = [{"type": "text", "text": text + note}]
    for msg_id, url in used:
        parts.append({"type": "text", "text": f"[msg:{msg_id}] 的图片："})
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def extract_session(llm: LLM, session_display: str, messages: list[Message],
                    budget: int, session: str, log=print,
                    pending: list[dict] | None = None
                    ) -> tuple[list[Task], list[dict], Usage]:
    """返回 (新待办, 取消列表, 用量)。取消项形如 {task_id, reason, msg_ids}。"""
    tasks: list[Task] = []
    cancelled: list[dict] = []
    usage = Usage()
    pending_ids = {int(t["id"]) for t in (pending or [])}
    batches = _pack(messages, budget)
    for i, batch in enumerate(batches, 1):
        log(f"[extract] {session_display} 批次 {i}/{len(batches)}（{len(batch)} 条）")
        data, u = llm.complete_json(
            SYSTEM_PROMPT, build_content(session_display, batch, pending=pending))
        usage.add(u)
        id_set = {m.local_id for m in batch}
        for raw in (data.get("cancelled") or []):
            try:
                tid = int(raw.get("task_id") or 0)
            except (TypeError, ValueError):
                continue
            ev_ids = [int(x) for x in (raw.get("msg_ids") or []) if int(x) in id_set]
            if tid not in pending_ids or not ev_ids:
                continue  # 清单里没有、或没有可核对原文的，一律不取消
            ev_text = " ".join(m.text for m in batch if m.local_id in set(ev_ids))
            import re as _re
            reason = (raw.get("reason") or "").strip()
            if not reason or _re.sub(r"\s+", "", reason) not in _re.sub(r"\s+", "", ev_text):
                log(f"[extract] 驳回对 #{tid} 的取消：理由不是逐字原文")
                continue
            cancelled.append({"task_id": tid, "reason": (raw.get("reason") or "").strip(),
                              "msg_ids": ev_ids})
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
    return tasks, cancelled, usage
