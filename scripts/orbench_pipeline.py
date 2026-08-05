#!/usr/bin/env python3
"""Compatibility entry; implementation lives in src/orbias."""

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
_SOURCE = _ROOT / "src" / "orbias" / "evaluation/pipeline.py"
exec(compile(_SOURCE.read_bytes(), str(_SOURCE), "exec"), globals(), globals())

