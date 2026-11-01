"""流水线编排：解密 → 白名单 → 增量取消息 → 抽取 → 去重入库 → 出 Markdown。

对外只暴露一个生成器 iter_run()，CLI 和 SSE 都消费它，保证两条路径行为一致。
"""
from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Iterator

from . import merge as merger
from . import render, scan, upstream
from .config import Config
from .decrypt import decrypt
from .extract import build_user_prompt, extract_session, _pack
from .llm import LLM
from .store import Store


def _ev(kind: str, **payload) -> dict:
    return {"type": kind, "ts": int(time.time()), **payload}


def iter_run(cfg: Config, *, skip_decrypt: bool = False, dry_run: bool = False,
             only_session: str | None = None) -> Iterator[dict]:
    cfg.require_whitelist()
    store = Store(cfg.state_db)
    run_id = store.start_run()
    started = time.time()
    stats: dict = {"run_id": run_id, "sessions": 0, "messages": 0, "tasks": 0,
                   "inserted": 0, "duplicates": 0, "skipped_sessions": 0}
    prompt_tokens = completion_tokens = 0
    ok = False
    try:
        yield _ev("log", msg=f"运行 {run_id} 开始")
        if not skip_decrypt:
            yield _ev("stage", stage="decrypt")
            decrypt(cfg, log=lambda m: None)
            yield _ev("log", msg="解密完成")
        else:
            yield _ev("log", msg="跳过解密，复用现有解密目录")

        yield _ev("stage", stage="scan")
        sessions = [s for s in scan.list_sessions(cfg.work_dir, cfg.whitelist) if s.whitelisted]
        if only_session:
            sessions = [s for s in sessions if only_session in (s.username, s.display)]
        if not sessions:
            raise RuntimeError("白名单没有命中任何会话，检查 config.toml 的 [filter].whitelist")
        yield _ev("log", msg=f"白名单命中 {len(sessions)} 个会话")
        stats["sessions"] = len(sessions)

        displays = scan._display_names(cfg.work_dir)
        senders = scan._sender_map(cfg.work_dir)
        cutoff = int(time.time()) - cfg.window_hours * 3600
        media_ctx = None
        if cfg.media_enabled:
            media_ctx = scan.make_media_context(cfg.work_dir, cfg.account_root(),
                                               upstream.prepare(cfg))
            if media_ctx is None:
                yield _ev("log", msg="媒体开启但未找到账号目录，非文本消息将跳过")
            else:
                yield _ev("log", msg=f"媒体已开启：{len(media_ctx.hardlink)} 条硬链接记录")
        else:
            yield _ev("log", msg="纯文本模式：非文本消息（图/文件/语音）跳过")

        llm = None if dry_run else LLM(cfg)
        all_tasks = []
        dumps = []

        for s in sessions:
            cursor = store.get_cursor(s.username)
            # sort_seq 是毫秒，create_time 是秒：首次运行（cursor=0）的窗口下限要换算
            floor = cursor if cursor else cutoff * 1000
            msgs = scan.fetch_messages(cfg.work_dir, s, floor, text_types=cfg.text_types,
                                       senders=senders, displays=displays,
                                       limit=cfg.max_messages_per_session,
                                       media=media_ctx,
                                       max_file_chars=cfg.max_file_chars,
                                       with_images=cfg.media_images,
                                       cache_file=cfg.media_cache)
            if not msgs:
                stats["skipped_sessions"] += 1
                yield _ev("log", msg=f"{s.display}：无新消息")
                continue
            stats["messages"] += len(msgs)
            yield _ev("log", msg=f"{s.display}：{len(msgs)} 条新消息")

            if dry_run:
                for batch in _pack(msgs, cfg.max_chars_per_request):
                    dumps.append(build_user_prompt(s.display, batch))
                continue
            tasks, usage = extract_session(
                llm, s.display, msgs, cfg.max_chars_per_request, s.username,
                log=lambda m: None)
            prompt_tokens += usage.prompt_tokens
            completion_tokens += usage.completion_tokens
            yield _ev("log", msg=f"{s.display}：抽到 {len(tasks)} 条待办，"
                                 f"tokens {usage.prompt_tokens}+{usage.completion_tokens}")
            all_tasks.extend(tasks)
            store.set_cursor(s.username, msgs[-1].sort_seq)

        if dry_run:
            out = cfg.work_dir.parent / "data" / "prompt_dump.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(dumps, ensure_ascii=False, indent=2), encoding="utf-8")
            yield _ev("log", msg=f"dry-run：请求包已落盘 {out}（{len(dumps)} 批）")
            stats["tasks"] = 0
            ok = True
            yield _ev("done", stats=stats)
            return

        inserted, dup = store.save_tasks(all_tasks, run_id)
        stats.update(tasks=len(all_tasks), inserted=inserted, duplicates=dup)

        yield _ev("stage", stage="merge")
        mstats, musage = merger.merge_pending(cfg, store, log=lambda m: None)
        prompt_tokens += musage.prompt_tokens
        completion_tokens += musage.completion_tokens
        stats["merge"] = mstats
        yield _ev("log", msg=(f"合并：{mstats['groups']} 组、吸收 {mstats['merged_away']} 条、"
                              f"提级 {mstats['lifted']} 条（候选 {mstats['candidates']}）"))

        yield _ev("stage", stage="render")
        fresh = store.tasks_of_run(run_id)
        path = render.write_daily(fresh, cfg.out_dir, dt.date.today())
        stats["markdown"] = str(path)
        yield _ev("log", msg=f"写入 Markdown：{path}")
        ok = True
        stats["elapsed"] = round(time.time() - started, 1)
        yield _ev("done", stats=stats)
    except Exception as exc:  # 顶层只做记录与上报，不做降级重试
        yield _ev("error", msg=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        store.finish_run(run_id, ok, stats, prompt_tokens, completion_tokens)


def run_once(cfg: Config, *, skip_decrypt: bool = False, dry_run: bool = False,
             log=print) -> dict:
    """CLI 用：把事件流打到控制台，返回最终统计。"""
    stats: dict = {}
    for ev in iter_run(cfg, skip_decrypt=skip_decrypt, dry_run=dry_run):
        t = ev["type"]
        if t == "log":
            log(ev["msg"])
        elif t == "stage":
            log(f"── 阶段：{ev['stage']} ──")
        elif t == "done":
            stats = ev["stats"]
    return stats
