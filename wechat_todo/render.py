"""产出层：Markdown 日报。所有条目都带来源会话与 msgid 反链。"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

PRIORITY_ORDER = [("high", "🔴 高"), ("medium", "🟡 中"), ("low", "⚪ 低")]


def render_markdown(tasks: list[dict], day: dt.date | None = None) -> str:
    day = day or dt.date.today()
    lines: list[str] = [f"# 微信待办 {day.isoformat()}", ""]
    if not tasks:
        lines.append("_本窗口内没有抽到新待办。_")
        return "\n".join(lines) + "\n"

    lines.append(f"共 {len(tasks)} 条待办。")
    lines.append("")

    by_prio: dict[str, list[dict]] = {}
    for t in tasks:
        by_prio.setdefault(t.get("priority") or "medium", []).append(t)

    for key, label in PRIORITY_ORDER:
        group = by_prio.get(key, [])
        if not group:
            continue
        lines.append(f"## {label}（{len(group)}）")
        lines.append("")
        for t in group:
            due = f" · 截止 `{t['due']}`" if t.get("due") else ""
            owner = f" · 责任人 {t['owner']}" if t.get("owner") else ""
            lines.append(f"- [ ] **{t['title']}**{due}{owner}")
            if t.get("quote"):
                ids = ",".join(str(x) for x in t.get("msg_ids", []))
                lines.append(f"  > {t['quote']}")
                lines.append(f"  > —— {t['session_display']} `msg:{ids}`")
        lines.append("")
    return "\n".join(lines)


def write_daily(tasks: list[dict], out_dir: Path, day: dt.date | None = None) -> Path:
    day = day or dt.date.today()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{day.isoformat()}.md"
    path.write_text(render_markdown(tasks, day), encoding="utf-8")
    return path
