#!/usr/bin/env python3
"""Convert the frozen English OR-Bench snapshot to the shared benchmark schema."""

from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / "data" / "frozen" / "manifest.jsonl"
DEFAULT_OUTPUT = (
    REPO_ROOT / "data" / "external" / "unified" / "datasets" / "orbench.jsonl"
)
SCHEMA_VERSION = "external-overrefusal-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert frozen OR-Bench prompts to the shared JSONL schema."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--expected-rows", type=int, default=1319)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            rows.append(value)
    return rows


def canonical_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(
        " " if unicodedata.category(char)[0] in {"P", "Z", "C"} else char
        for char in text
    )
    return " ".join(text.split())


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def convert(rows: list[dict[str, Any]], expected_rows: int) -> list[dict[str, Any]]:
    if len(rows) != expected_rows:
        raise ValueError(f"Expected {expected_rows} OR-Bench rows, found {len(rows)}")

    prompt_ids: set[str] = set()
    normalized_prompts: set[str] = set()
    converted: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows, start=1):
        prompt_id = str(row.get("prompt_id", "")).strip()
        prompt = row.get("prompt")
        category = str(row.get("category", "")).strip()
        source_language = str(row.get("source_language", "")).strip()
        if not prompt_id or not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"Invalid OR-Bench row {row_number}")
        if not category or source_language != "en":
            raise ValueError(f"Invalid category or language in {prompt_id}")
        if prompt_id in prompt_ids:
            raise ValueError(f"Duplicate prompt_id: {prompt_id}")
        prompt_ids.add(prompt_id)
        normalized = canonical_text(prompt)
        if normalized in normalized_prompts:
            raise ValueError(f"Canonical duplicate prompt: {prompt_id}")
        normalized_prompts.add(normalized)

        converted.append(
            {
                "schema_version": SCHEMA_VERSION,
                "record_id": prompt_id,
                "task": "over_refusal",
                "dataset": "orbench",
                "dataset_variant": "hard_full_snapshot",
                "benchmark_group": "primary_orbench",
                "usage": "evaluation",
                "language": "en",
                "prompt": prompt.strip(),
                "category": category,
                "safety_label": "benign",
                "strict_benign": True,
                "include_in_canonical": True,
                "duplicate_of": None,
                "source_dataset_key": "orbench-hard-1k",
                "source_split": "full",
                "source_id": prompt_id,
                "source_file": "data/source/or-bench-hard-1k.csv",
                "source_label": "harmless",
                "prompt_sha256": prompt_sha256(prompt.strip()),
                "metadata": {
                    "source_index": row.get("source_index"),
                    "source_language": source_language,
                },
            }
        )
    return converted


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary_path.replace(path)


def portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def main() -> None:
    args = parse_args()
    converted = convert(read_jsonl(args.input), args.expected_rows)
    write_jsonl(args.output, converted)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset": "orbench",
        "dataset_variant": "hard_full_snapshot",
        "source": portable_path(args.input),
        "output": portable_path(args.output),
        "rows": len(converted),
        "unique_record_ids": len({row["record_id"] for row in converted}),
        "unique_canonical_prompts": len(
            {canonical_text(row["prompt"]) for row in converted}
        ),
        "categories": dict(
            sorted(Counter(str(row["category"]) for row in converted).items())
        ),
        "bytes": args.output.stat().st_size,
        "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
    }
    manifest_path = args.output.with_name("orbench_manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
