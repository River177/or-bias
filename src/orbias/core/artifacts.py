"""Append-only JSONL, atomic state, locking, and content manifests."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class ArtifactSummary:
    path: str
    size_bytes: int
    lines: int
    sha256: str


class FileLock:
    """Exclusive create lock whose contents identify the owner."""

    def __init__(self, path: Path, *, stale_after_seconds: float | None = None):
        self.path = path
        self.stale_after_seconds = stale_after_seconds
        self._held = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"pid": os.getpid(), "created_at": time.time()}
        while True:
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if self.stale_after_seconds is None:
                    raise RuntimeError(f"Lock already held: {self.path}")
                age = time.time() - self.path.stat().st_mtime
                if age <= self.stale_after_seconds:
                    raise RuntimeError(f"Lock already held: {self.path}")
                self.path.unlink()
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._held = True
            return

    def release(self) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


class ArtifactStore:
    """Own all local artifact invariants behind one small interface."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def path(self, relative: str | Path) -> Path:
        candidate = (self.root / relative).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(f"Artifact path escapes root: {relative}")
        return candidate

    def read_jsonl(self, relative: str | Path, *, missing_ok: bool = True) -> list[JsonObject]:
        path = self.path(relative)
        if missing_ok and not path.exists():
            return []
        rows: list[JsonObject] = []
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"{path}:{line_number} is not a JSON object")
                rows.append(value)
        return rows

    def append_unique(
        self,
        relative: str | Path,
        rows: Iterable[JsonObject],
        *,
        key: Callable[[JsonObject], Any],
    ) -> int:
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = {key(row) for row in self.read_jsonl(relative)}
        pending: list[JsonObject] = []
        for row in rows:
            row_key = key(row)
            if row_key in existing:
                continue
            existing.add(row_key)
            pending.append(row)
        if not pending:
            return 0
        with path.open("a", encoding="utf-8") as handle:
            for row in pending:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return len(pending)

    def append(self, relative: str | Path, row: JsonObject) -> None:
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def write_json_atomic(self, relative: str | Path, value: Any) -> Path:
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        return path

    def keys(self, relative: str | Path, key: Callable[[JsonObject], Any]) -> set[Any]:
        rows = self.read_jsonl(relative)
        result = {key(row) for row in rows}
        if len(result) != len(rows):
            raise ValueError(f"Duplicate keys in {self.path(relative)}")
        return result

    def summarize(self, relative: str | Path) -> ArtifactSummary:
        path = self.path(relative)
        digest = hashlib.sha256()
        lines = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                lines += chunk.count(b"\n")
        return ArtifactSummary(
            path=str(relative), size_bytes=path.stat().st_size, lines=lines, sha256=digest.hexdigest()
        )

    def manifest(self, files: Sequence[str | Path], output: str | Path) -> list[ArtifactSummary]:
        summaries = [self.summarize(item) for item in files]
        self.write_json_atomic(output, {"artifacts": [asdict(item) for item in summaries]})
        return summaries

    def verify(self, expected: Sequence[ArtifactSummary | JsonObject]) -> None:
        for item in expected:
            wanted = item if isinstance(item, ArtifactSummary) else ArtifactSummary(**item)
            actual = self.summarize(wanted.path)
            if actual != wanted:
                raise ValueError(f"Artifact verification failed for {wanted.path}: {actual} != {wanted}")

    def lock(self, relative: str | Path, *, stale_after_seconds: float | None = None) -> FileLock:
        return FileLock(self.path(relative), stale_after_seconds=stale_after_seconds)
