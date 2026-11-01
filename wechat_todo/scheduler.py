"""内置定时器：支持「固定间隔」和「每天固定时刻」两种，取最近的一次。

- interval_minutes > 0：每 N 分钟跑一次
- daily_times = ["09:00", "21:30"]：每天这些时刻跑
两者可以同时用，取最近的触发点。都为 0/空则视为只用手动运行。
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Callable


def _next_daily(times: list[str], now: float) -> float | None:
    """算下一次命中的时间戳。"""
    points: list[float] = []
    base = dt.datetime.fromtimestamp(now)
    for raw in times:
        try:
            hh, mm = (int(x) for x in raw.strip().split(":"))
        except (ValueError, AttributeError):
            continue
        cand = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand.timestamp() <= now:
            cand += dt.timedelta(days=1)
        points.append(cand.timestamp())
    return min(points) if points else None


class Scheduler:
    def __init__(self, run_cb: Callable[[], None], *, interval_minutes: int = 0,
                 daily_times: list[str] | None = None, run_on_start: bool = False,
                 busy: Callable[[], bool] | None = None, log=print):
        self._run_cb = run_cb
        self._busy = busy or (lambda: False)
        self.interval = max(0, int(interval_minutes))
        self.daily_times = list(daily_times or [])
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

    # ---------- 配置 ----------
    def configure(self, *, interval_minutes: int | None = None,
                  daily_times: list[str] | None = None) -> None:
        if interval_minutes is not None:
            self.interval = max(0, int(interval_minutes))
        if daily_times is not None:
            self.daily_times = [t.strip() for t in daily_times if t and t.strip()]
        if self.enabled:  # 让新计划立即生效
            self.stop()
            self.start(run_now=False)

    def _next_fire(self, now: float) -> float | None:
        points = []
        if self.interval > 0:
            points.append(now + self.interval * 60)
        daily = _next_daily(self.daily_times, now)
        if daily is not None:
            points.append(daily)
        return min(points) if points else None

    # ---------- 生命周期 ----------
    def start(self, *, run_now: bool | None = None) -> None:
        if self.enabled:
            return
        if self.interval <= 0 and not self.daily_times:
            raise ValueError("既没有间隔也没有固定时刻，无法定时")
        self.enabled = True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True,
            args=(self.run_on_start if run_now is None else run_now,))
        self._thread.start()
        plan = []
        if self.interval:
            plan.append(f"每 {self.interval} 分钟")
        if self.daily_times:
            plan.append("每天 " + "/".join(self.daily_times))
        self.log("[schedule] 已启动：" + "、".join(plan))

    def stop(self) -> None:
        if not self.enabled:
            return
        self.enabled = False
        self._stop.set()
        self.next_at = None
        self.log("[schedule] 已停止")

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "interval_minutes": self.interval,
            "daily_times": self.daily_times,
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
            now = time.time()
            nxt = self._next_fire(now)
            if nxt is None:
                break
            self.next_at = nxt
            if self._stop.wait(max(1.0, nxt - now)):
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
