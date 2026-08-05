"""Canonical repository and artifact path resolution."""

from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = REPO_ROOT.parent / "or-bias-artifacts"


def artifact_root(cli_value: str | Path | None = None) -> Path:
    """Resolve artifact root with CLI > environment > sibling priority."""
    if cli_value is not None:
        return Path(cli_value).expanduser().resolve()
    configured = os.environ.get("ORBIAS_ARTIFACT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_ARTIFACT_ROOT.resolve()


def repo_path(value: str | Path) -> Path:
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else REPO_ROOT / candidate


def artifact_path(value: str | Path, cli_value: str | Path | None = None) -> Path:
    candidate = Path(value).expanduser()
    return candidate.resolve() if candidate.is_absolute() else artifact_root(cli_value) / candidate
