"""取消判定闸门：模型说"要取消"，还必须在证据原文里找到明确的否定/替代措辞。

为什么需要：
把判定完全交给模型会误杀（实测把"要换这个吗 换 你去下单"当成取消。
那实际是确认下单）。所以规则拆成两半：
- 模型负责"这两句是不是在说同一件事"
- 这道闸门负责"有没有真的说不"——词是确定的，可审计、可复算
"""
from __future__ import annotations

# 明确否定
NEGATIONS = ("算了", "不用", "不要了", "别弄", "别搞", "先不", "不搞",
             "改主意", "先放", "取消", "还是别", "暂停", "不做了", "搁置")
# 方案替代（B 口径）：新做法取代旧做法
REPLACEMENTS = ("改成", "改为", "换成", "换用", "改用", "改成私有", "不公开",
                "单独开", "换个", "不要这个")

MARKERS = NEGATIONS + REPLACEMENTS


def has_marker(text: str) -> bool:
    return any(m in text for m in MARKERS)


def hit_markers(text: str) -> list[str]:
    return [m for m in MARKERS if m in text]


def accept(evidence_text: str) -> tuple[bool, str]:
    """返回 (是否放行, 说明)。"""
    hits = hit_markers(evidence_text)
    if hits:
        return True, "命中：" + "/".join(hits[:3])
    return False, "证据里没有否定或替代措辞（只有“换/要”这类不含打消意味的词不算）"
