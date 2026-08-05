"""Download and verify frozen datasets and audit assets from GitHub Releases."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from orbias.paths import REPO_ROOT, artifact_root


METADATA_DIR = REPO_ROOT / "data" / "releases"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_metadata(name: str) -> dict[str, Any]:
    frozen_metadata = REPO_ROOT / "data" / "frozen" / name / "release.json"
    path = frozen_metadata if frozen_metadata.exists() else METADATA_DIR / f"{name}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Invalid release metadata: {path}")
    return value


def _safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as handle:
        for member in handle.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Unsafe archive member: {member.name}")
        try:
            handle.extractall(destination, filter="data")
        except TypeError:  # Python 3.9-3.11: members were validated above.
            handle.extractall(destination)


def fetch(name: str, *, output_root: Path | None = None) -> list[Path]:
    metadata = load_metadata(name)
    base = output_root or artifact_root()
    downloaded: list[Path] = []
    for asset in metadata["assets"]:
        destination_root = REPO_ROOT if asset.get("scope") == "repo" else base
        destination = destination_root / str(asset["destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
        try:
            try:
                with urllib.request.urlopen(str(asset["url"]), timeout=120) as response:
                    with temporary_path.open("wb") as handle:
                        shutil.copyfileobj(response, handle)
            except urllib.error.HTTPError as exc:
                if exc.code != 404 or not metadata.get("draft"):
                    raise
                subprocess.run(
                    [
                        "gh", "release", "download", str(metadata["tag"]),
                        "--repo", str(metadata["repository"]),
                        "--pattern", str(asset["name"]),
                        "--output", str(temporary_path),
                        "--clobber",
                    ],
                    check=True,
                )
            actual = sha256(temporary_path)
            if actual != asset["sha256"]:
                raise ValueError(f"SHA256 mismatch for {asset['name']}: {actual}")
            if asset.get("archive") == "tar.gz":
                _safe_extract(temporary_path, destination)
            else:
                temporary_path.replace(destination)
            downloaded.append(destination)
        finally:
            temporary_path.unlink(missing_ok=True)
    return downloaded


def verify(name: str, *, output_root: Path | None = None) -> list[dict[str, Any]]:
    metadata = load_metadata(name)
    base = output_root or artifact_root()
    results: list[dict[str, Any]] = []
    for item in metadata.get("installed_files", []):
        destination_root = REPO_ROOT if item.get("scope") == "repo" else base
        path = destination_root / str(item["path"])
        actual = sha256(path)
        if actual != item["sha256"]:
            raise ValueError(f"SHA256 mismatch for {path}: {actual}")
        results.append({"path": str(path), "size_bytes": path.stat().st_size, "sha256": actual})
    return results
