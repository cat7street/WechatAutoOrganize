"""流水线编排：解密 → 白名单 → 增量取消息 → 抽取 → 去重入库 → 出 Markdown。

对外只暴露一个生成器 iter_run()，CLI 和 SSE 都消费它，保证两条路径行为一致。
"""
from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Iterator

from . import fullsync
from . import merge as merger
from . import reconcile as reconciler
from . import render, scan, upstream
from .config import Config
from .decrypt import run_detached
from .extract import build_user_prompt, extract_session, _pack
from .llm import LLM
from .store import Store
from .walsync import sync as wal_sync


def _ev(kind: str, **payload) -> dict:
    return {"type": kind, "ts": int(time.time()), **payload}


def iter_run(cfg: Config, *, skip_decrypt: bool = False, dry_run: bool = False,
             only_session: str | None = None, mode: str | None = None) -> Iterator[dict]:
    """mode = "incremental"（只抽新消息）| "full"（48h 全量对账，定时任务用这个）。"""
    mode = mode or cfg.pipeline_mode
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
            dstats = run_detached(cfg, log=lambda m: None)
            stats["decrypt"] = dstats
            yield _ev("log", msg="解密完成（后台进程已退出）")
        else:
            yield _ev("log", msg="跳过解密，复用现有解密目录")

        # 子进程已经做过 WAL 合并；只有“跳过解密”的那一轮需要在这里补
        if skip_decrypt:
            yield _ev("stage", stage="wal")
            try:
                wal_stats = wal_sync(cfg.work_dir, cfg.db_dir, cfg.keys_file,
                                     log=lambda m: None)
                stats["wal"] = wal_stats
                yield _ev("log", msg=(f"WAL 合并：{wal_stats['patched']} 个库更新"
                                       f"（有 WAL 的库 {wal_stats['checked'] - wal_stats['no_wal']}）"))
                if wal_stats["errors"]:
                    yield _ev("log", msg="WAL 警告：" + "；".join(wal_stats["errors"][:3]))
            except FileNotFoundError as exc:
                raise RuntimeError(f"WAL 合并失败：{exc}") from exc

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

        # ── 全量对账：48h 完整对话 + 当前清单 → 模型整体更新 ──
        if mode == "full" and not dry_run:
            yield _ev("stage", stage="sync")
            sstats, susage, newest = fullsync.sync(
                cfg, store, log=lambda m: None, hours=cfg.window_hours,
                max_context_chars=cfg.reconcile_max_chars,
                tasks_per_call=cfg.reconcile_tasks_per_call,
                max_calls=cfg.reconcile_max_calls, session_filter=only_session)
            prompt_tokens += susage.prompt_tokens
            completion_tokens += susage.completion_tokens
            stats["sync"] = sstats
            if newest:
                for s in sessions:
                    store.set_cursor(s.username, newest)
            yield _ev("log", msg=(f"全量对账（{cfg.window_hours}h）：新增 {sstats['inserted']}、"
                                  f"更新 {sstats['updated']}、取消 {sstats['cancelled']}、"
                                  f"被替代 {sstats['superseded']}、恢复 {sstats['restored']}、"
                                  f"驳回 {sstats['rejected']}（{sstats['calls']} 次调用）"))
            sessions = []  # 全量模式不再逐会话抽增量

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
            tasks, cancelled, usage = extract_session(
                llm, s.display, msgs, cfg.max_chars_per_request, s.username,
                log=lambda m: None, pending=store.list_pending_brief())
            prompt_tokens += usage.prompt_tokens
            completion_tokens += usage.completion_tokens
            for c in cancelled:
                if store.cancel_task(c["task_id"], c["reason"], c["msg_ids"]):
                    stats.setdefault("cancelled", 0)
                    stats["cancelled"] += 1
                    yield _ev("log", msg=(f"取消待办 #{c['task_id']}：《{c['reason'] or '被推翻'}》"
                                           f"（msg:{','.join(map(str, c['msg_ids']))}）"))
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

        # 回溯核对：后续对话里被明确推翻的待办，不要留在待办列表里
        rstats, rusage = {"tasks": 0, "calls": 0, "cancelled": 0, "superseded": 0,
                          "restored": 0, "suspected": 0, "rejected": 0}, None
        if cfg.reconcile_enabled and mode != "full":
            yield _ev("stage", stage="reconcile")
            rstats, rusage = reconciler.reconcile(
                cfg, store, log=lambda m: None,
                window_hours=cfg.reconcile_window_hours,
                max_context_chars=cfg.reconcile_max_chars,
                tasks_per_call=cfg.reconcile_tasks_per_call,
                max_calls=cfg.reconcile_max_calls)
        if rusage is not None:
            prompt_tokens += rusage.prompt_tokens
            completion_tokens += rusage.completion_tokens
        stats["reconcile"] = rstats
        yield _ev("log", msg=(f"回溯核对：核对 {rstats['tasks']} 条待办 / {rstats['calls']} 次调用 → "
                              f"取消 {rstats['cancelled']}、被替代 {rstats['superseded']}、"
                              f"恢复 {rstats['restored']}、疑似 {rstats['suspected']}、"
                              f"驳回 {rstats['rejected']}"))

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
             mode: str | None = None, log=print) -> dict:
    """CLI 用：把事件流打到控制台，返回最终统计。"""
    stats: dict = {}
    for ev in iter_run(cfg, skip_decrypt=skip_decrypt, dry_run=dry_run, mode=mode):
        t = ev["type"]
        if t == "log":
            log(ev["msg"])
        elif t == "stage":
            log(f"── 阶段：{ev['stage']} ──")
        elif t == "done":
            stats = ev["stats"]
    return stats
