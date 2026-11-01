"""内置定时器：按间隔自动跑一轮"解密 → WAL 合并 → 抽取 → 合并去重 → 回溯核对 → 出日报"。

设计：
- 单线程循环 + Event.wait(interval)，可随时 stop/改间隔
- 与手动运行互斥：正在跑就跳过这一轮（skip，不排队堆积）
- 每轮结果都写进 runs 表，面板能看到历史和失败原因
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable


class Scheduler:
    def __init__(self, run_cb: Callable[[], None], *, interval_minutes: int = 60,
                 run_on_start: bool = True, busy: Callable[[], bool] | None = None,
                 log=print):
        self._run_cb = run_cb
        self._busy = busy or (lambda: False)
        self.interval = max(1, int(interval_minutes))
        self.run_on_start = run_on_start
        self.log = log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.enabled = False
        self.next_at: float | None = None
        self.last_at: float | None = None
        self.last_result: str = ""
        self.skipped = 0
        self.runs = 0

    # ---------- 生命周期 ----------
    def start(self, *, run_now: bool | None = None) -> None:
        if self.enabled:
            return
        self.enabled = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        args=(self.run_on_start if run_now is None else run_now,))
        self._thread.start()
        self.log(f"[schedule] 已启动，每 {self.interval} 分钟一次")

    def stop(self) -> None:
        if not self.enabled:
            return
        self.enabled = False
        self._stop.set()
        self.next_at = None
        self.log("[schedule] 已停止")

    def set_interval(self, minutes: int) -> None:
        self.interval = max(1, int(minutes))
        if self.enabled:  # 重启线程让新间隔立即生效
            self.stop()
            self.start(run_now=False)

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "interval_minutes": self.interval,
            "next_at": int(self.next_at) if self.next_at else None,
            "last_at": int(self.last_at) if self.last_at else None,
            "last_result": self.last_result,
            "runs": self.runs,
            "skipped": self.skipped,
        }

    # ---------- 主体 ----------
    def _loop(self, run_now: bool) -> None:
        if run_now:
            self._tick()
        while not self._stop.is_set():
            self.next_at = time.time() + self.interval * 60
            if self._stop.wait(self.interval * 60):
                break
            self._tick()
        self.next_at = None

    def _tick(self) -> None:
        if self._busy():
            self.skipped += 1
            self.last_result = "跳过：上一次还在跑"
            self.log("[schedule] 上一轮还没结束，跳过本次")
            return
        self.last_at = time.time()
        try:
            self._run_cb()
            self.runs += 1
            self.last_result = "成功"
        except Exception as exc:  # 定时任务不能把线程打死
            self.last_result = f"失败：{type(exc).__name__}: {exc}"
            self.log(f"[schedule] 本轮失败：{exc}")
