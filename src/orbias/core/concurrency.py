"""Reusable rate limiting and congestion control policies."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any


class RateLimiter:
    def __init__(self, interval_seconds: float):
        self.interval_seconds = float(interval_seconds)
        self.lock = threading.Lock()
        self.last_start = 0.0

    def wait(self) -> None:
        if self.interval_seconds <= 0:
            return
        with self.lock:
            delay = self.interval_seconds - (time.monotonic() - self.last_start)
            if delay > 0:
                time.sleep(delay)
            self.last_start = time.monotonic()


class CongestionController:
    """TCP-Reno-inspired controller used by translation batches."""

    def __init__(self, *, initial_cwnd: float, ssthresh: float, min_cwnd: float, max_cwnd: float, window_size: int) -> None:
        self.cwnd = float(initial_cwnd)
        self.ssthresh = float(ssthresh)
        self.min_cwnd = float(min_cwnd)
        self.max_cwnd = float(max_cwnd)
        self.window_size = int(window_size)
        self.outcomes: deque[bool] = deque(maxlen=self.window_size)
        self.latencies: deque[float] = deque(maxlen=self.window_size)
        self.cooldown_until = 0.0
        self.consecutive_congestion = 0

    @property
    def target(self) -> int:
        return max(1, int(math.floor(self.cwnd)))

    def record_success(self, latency_seconds: float) -> None:
        self.outcomes.append(True)
        self.latencies.append(float(latency_seconds))
        self.consecutive_congestion = 0
        recent_error_rate = 1.0 - (sum(self.outcomes) / len(self.outcomes))
        if recent_error_rate >= 0.02:
            return
        increment = (1.0 if self.cwnd < self.ssthresh else 0.5) / max(self.cwnd, 1.0)
        self.cwnd = min(self.max_cwnd, self.cwnd + increment)

    def record_congestion(self, kind: str, retry_after: float = 0.0) -> None:
        self.outcomes.append(False)
        self.consecutive_congestion += 1
        if kind == "429":
            self.ssthresh = max(2.0, self.cwnd / 2.0)
            self.cwnd = max(self.min_cwnd, self.cwnd / 2.0)
        elif self.consecutive_congestion >= 3:
            self.ssthresh = max(2.0, self.cwnd / 2.0)
            self.cwnd = max(self.min_cwnd, self.cwnd / 2.0)
        else:
            self.cwnd = max(self.min_cwnd, self.cwnd * 0.7)
        delay = max(float(retry_after), 1.0 if kind == "429" else 0.0)
        self.cooldown_until = max(self.cooldown_until, time.time() + delay)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cwnd": self.cwnd,
            "ssthresh": self.ssthresh,
            "min_cwnd": self.min_cwnd,
            "max_cwnd": self.max_cwnd,
            "target": self.target,
            "cooldown_until": self.cooldown_until,
            "recent_successes": sum(self.outcomes),
            "recent_requests": len(self.outcomes),
            "consecutive_congestion": self.consecutive_congestion,
        }


class AdaptiveConcurrency:
    """Thread-safe TCP-Reno AIMD gate for model requests."""

    def __init__(self, initial: int = 8, *, max_concurrency: int | None = None, ssthresh: float | None = None, state: dict[str, Any] | None = None):
        self._min_cwnd = 1.0
        self._max_cwnd = float(max_concurrency or initial)
        self._cwnd = min(self._max_cwnd, max(self._min_cwnd, float(initial)))
        self._ssthresh = min(self._max_cwnd, max(2.0, float(ssthresh or self._max_cwnd)))
        self._active = 0
        self._recent: deque[bool] = deque(maxlen=20)
        self._recovery_remaining = 0
        self._total_requests = self._total_successes = self._total_congestion = 0
        self._lock = threading.Condition()
        if state:
            self._cwnd = min(self._max_cwnd, max(self._min_cwnd, float(state.get("cwnd", self._cwnd))))
            self._ssthresh = min(self._max_cwnd, max(2.0, float(state.get("ssthresh", self._ssthresh))))
            self._recovery_remaining = max(0, int(state.get("recovery_remaining", 0)))
            self._total_requests = max(0, int(state.get("total_requests", 0)))
            self._total_successes = max(0, int(state.get("total_successes", 0)))
            self._total_congestion = max(0, int(state.get("total_congestion", 0)))

    @property
    def target(self) -> int:
        with self._lock:
            return max(1, int(math.floor(self._cwnd)))

    @property
    def cwnd(self) -> float:
        with self._lock:
            return self._cwnd

    @property
    def ssthresh(self) -> float:
        with self._lock:
            return self._ssthresh

    def acquire(self) -> None:
        with self._lock:
            while self._active >= max(1, int(math.floor(self._cwnd))):
                self._lock.wait()
            self._active += 1

    def release(self) -> None:
        with self._lock:
            self._active -= 1
            self._lock.notify_all()

    def record(self, success: bool, retryable: bool) -> str:
        with self._lock:
            self._total_requests += 1
            self._recent.append(not success)
            event = "steady"
            if success:
                self._total_successes += 1
                if self._recovery_remaining > 0:
                    self._recovery_remaining -= 1
                    event = "recovery_ack"
                elif self._cwnd < self._ssthresh:
                    self._cwnd = min(self._max_cwnd, self._cwnd + 1.0)
                    event = "slow_start"
                elif self._cwnd < self._max_cwnd:
                    self._cwnd = min(self._max_cwnd, self._cwnd + 1.0 / self._cwnd)
                    event = "additive_increase"
            elif retryable:
                self._total_congestion += 1
                if self._recovery_remaining == 0:
                    self._ssthresh = max(2.0, self._cwnd / 2.0)
                    self._cwnd = max(self._min_cwnd, self._ssthresh)
                    self._recovery_remaining = max(1, int(math.ceil(self._cwnd)))
                    event = "multiplicative_decrease"
                else:
                    event = "congestion_during_recovery"
            else:
                event = "non_congestion_error"
            self._lock.notify_all()
            return event

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "algorithm": "tcp-reno-aimd-v1",
                "cwnd": self._cwnd,
                "ssthresh": self._ssthresh,
                "min_cwnd": self._min_cwnd,
                "max_cwnd": self._max_cwnd,
                "target": max(1, int(math.floor(self._cwnd))),
                "active": self._active,
                "recovery_remaining": self._recovery_remaining,
                "recent_error_rate": sum(self._recent) / len(self._recent) if self._recent else 0.0,
                "total_requests": self._total_requests,
                "total_successes": self._total_successes,
                "total_congestion": self._total_congestion,
                "updated_at_unix": time.time(),
            }
