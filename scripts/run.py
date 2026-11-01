#!/usr/bin/env python
"""CLI 入口：列表 / 干跑 / 正式跑一次增量。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wechat_todo import scan  # noqa: E402
from wechat_todo.config import ConfigError, load  # noqa: E402
from wechat_todo.pipeline import run_once  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="微信聊天记录 → 待办")
    ap.add_argument("--config", type=Path, help="config.toml 路径")
    ap.add_argument("--list-sessions", action="store_true", help="列出全部会话后退出")
    ap.add_argument("--skip-decrypt", action="store_true", help="复用现有解密目录")
    ap.add_argument("--dry-run", action="store_true", help="只组装请求包，不调模型")
    ap.add_argument("--session", help="只处理某个会话（username 或备注名）")
    args = ap.parse_args()

    try:
        cfg = load(args.config)
    except ConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2

    if args.list_sessions:
        if not cfg.work_dir.is_dir():
            print(f"[错误] 解密目录不存在：{cfg.work_dir}", file=sys.stderr)
            return 2
        rows = scan.list_sessions(cfg.work_dir, cfg.whitelist)
        print(f"{'纳入':<4} {'消息数':>7}  {'最后消息':<17} 会话")
        for s in rows:
            mark = "✔" if s.whitelisted else " "
            day = __import__("datetime").datetime.fromtimestamp(s.last_ts).strftime(
                "%Y-%m-%d %H:%M") if s.last_ts else "-"
            print(f"{mark:<4} {s.msg_count:>7}  {day:<17} {s.display}  [{s.username}]")
        print(f"\n共 {len(rows)} 个会话，白名单命中 {sum(1 for s in rows if s.whitelisted)} 个")
        return 0

    try:
        stats = run_once(cfg, skip_decrypt=args.skip_decrypt, dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2
    print("\n== 统计 ==")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
