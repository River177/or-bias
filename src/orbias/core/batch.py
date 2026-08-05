"""Resumable concurrent execution with error isolation and AIMD control."""

from __future__ import annotations

import random
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable

from .artifacts import ArtifactStore, JsonObject


class ErrorDisposition(str, Enum):
    RETRYABLE = "retryable"
    NONRETRYABLE = "nonretryable"


@dataclass(frozen=True)
class RunSummary:
    submitted: int
    succeeded: int
    failed: int
    retried: int
    skipped: int


class AIMDController:
    """Persistent-friendly TCP Reno style concurrency window."""

    def __init__(self, initial: int, *, minimum: int = 1, maximum: int = 64):
        if not minimum <= initial <= maximum:
            raise ValueError("initial concurrency must be within min/max")
        self.minimum = minimum
        self.maximum = maximum
        self.window = float(initial)
        self._success_credit = 0.0

    @property
    def target(self) -> int:
        return max(self.minimum, min(self.maximum, int(self.window)))

    def success(self) -> None:
        self._success_credit += 1.0 / max(1.0, self.window)
        if self._success_credit >= 1.0:
            self.window = min(float(self.maximum), self.window + 1.0)
            self._success_credit -= 1.0

    def congestion(self) -> None:
        self.window = max(float(self.minimum), self.window / 2.0)
        self._success_credit = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"algorithm": "tcp-reno-aimd-v1", "window": self.window, "target": self.target}


class ResumableBatchRunner:
    """Run keyed tasks append-only; retries never become success placeholders."""

    def __init__(
        self,
        store: ArtifactStore,
        *,
        success_path: str,
        error_path: str,
        key: Callable[[JsonObject], Any],
        worker: Callable[[JsonObject], JsonObject],
        classify_error: Callable[[BaseException], ErrorDisposition],
        concurrency: int = 8,
        max_attempts: int = 5,
        backoff_seconds: float = 1.0,
        controller: AIMDController | None = None,
    ):
        self.store = store
        self.success_path = success_path
        self.error_path = error_path
        self.key = key
        self.worker = worker
        self.classify_error = classify_error
        self.concurrency = concurrency
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.controller = controller

    def run(self, tasks: Iterable[JsonObject]) -> RunSummary:
        completed = {self.key(row) for row in self.store.read_jsonl(self.success_path)}
        queue: deque[tuple[JsonObject, int, float]] = deque()
        skipped = 0
        for task in tasks:
            if self.key(task) in completed:
                skipped += 1
            else:
                queue.append((task, 1, 0.0))
        submitted = len(queue)
        succeeded = failed = retried = 0
        active: dict[Future[JsonObject], tuple[JsonObject, int]] = {}
        workers = self.controller.maximum if self.controller else self.concurrency
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while queue or active:
                target = self.controller.target if self.controller else self.concurrency
                now = time.monotonic()
                while queue and len(active) < target:
                    task, attempt, ready_at = queue[0]
                    if ready_at > now:
                        break
                    queue.popleft()
                    active[pool.submit(self.worker, task)] = (task, attempt)
                if not active:
                    if queue:
                        time.sleep(max(0.0, min(0.1, queue[0][2] - time.monotonic())))
                    continue
                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    task, attempt = active.pop(future)
                    try:
                        row = future.result()
                    except BaseException as exc:
                        disposition = self.classify_error(exc)
                        if disposition is ErrorDisposition.RETRYABLE and attempt < self.max_attempts:
                            retried += 1
                            if self.controller:
                                self.controller.congestion()
                            delay = self.backoff_seconds * (2 ** (attempt - 1)) + random.random()
                            queue.append((task, attempt + 1, time.monotonic() + delay))
                            continue
                        failed += 1
                        self.store.append_unique(
                            self.error_path,
                            [{**task, "attempt": attempt, "error": str(exc), "disposition": disposition.value}],
                            key=lambda row: (self.key(row), row["attempt"]),
                        )
                        continue
                    self.store.append_unique(self.success_path, [row], key=self.key)
                    completed.add(self.key(row))
                    succeeded += 1
                    if self.controller:
                        self.controller.success()
        return RunSummary(submitted, succeeded, failed, retried, skipped)

