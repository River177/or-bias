#!/usr/bin/env python3
"""Convert selected external over-refusal datasets to one canonical schema."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / "data" / "external" / "selected" / "all_selected.jsonl"
DEFAULT_EXCLUSIONS = (
    REPO_ROOT
    / "data"
    / "external"
    / "duplicate_audit"
    / "internal_duplicate_exclusions.jsonl"
)
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "external" / "unified"
SCHEMA_VERSION = "external-overrefusal-v1"


DATASET_POLICY: dict[str, dict[str, str]] = {
    "xstest-safe": {
        "dataset": "xstest",
        "variant": "safe",
        "benchmark_group": "general_fixed",
    },
    "phtest-harmless": {
        "dataset": "phtest",
        "variant": "harmless",
        "benchmark_group": "general_fixed",
    },
    "falsereject-all": {
        "dataset": "falsereject",
        "variant": "all",
        "benchmark_group": "general_fixed",
    },
    "overbench-hard": {
        "dataset": "overbench",
        "variant": "hard",
        "benchmark_group": "adaptive_stress",
    },
    "oktest-all": {
        "dataset": "oktest",
        "variant": "all",
        "benchmark_group": "general_fixed",
    },
    "health-orsc-benign-all": {
        "dataset": "health_orsc",
        "variant": "benign_full",
        "benchmark_group": "domain_health",
    },
    "bio-overrefusal-all": {
        "dataset": "bio_overrefusal",
        "variant": "all",
        "benchmark_group": "domain_biology",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build canonical JSONL views for external over-refusal datasets."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--duplicate-exclusions", type=Path, default=DEFAULT_EXCLUSIONS
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
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


def usage_for(source_dataset: str, source_split: str) -> str:
    if source_dataset == "falsereject-all" and source_split == "train":
        return "training"
    return "evaluation"


def normalize_category(value: Any) -> str | None:
    if value is None:
        return None
    category = str(value).strip()
    return category or None


def convert_row(
    row: dict[str, Any], duplicate_of: str | None
) -> dict[str, Any]:
    selection_id = str(row.get("selection_id", "")).strip()
    source_dataset = str(row.get("dataset", "")).strip()
    source_split = str(row.get("source_split", "")).strip()
    source_id = str(row.get("source_id", "")).strip()
    prompt = row.get("prompt")
    if not selection_id or not source_split or not source_id:
        raise ValueError(f"Missing stable provenance fields in {selection_id or row!r}")
    if source_dataset not in DATASET_POLICY:
        raise ValueError(f"Unsupported selected dataset: {source_dataset}")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"Missing prompt in {selection_id}")

    policy = DATASET_POLICY[source_dataset]
    strict_benign = bool(row.get("primary_benign_eligible"))
    safety_label = "benign" if strict_benign else str(
        row.get("evaluation_role") or "ambiguous"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "record_id": selection_id,
        "task": "over_refusal",
        "dataset": policy["dataset"],
        "dataset_variant": policy["variant"],
        "benchmark_group": policy["benchmark_group"],
        "usage": usage_for(source_dataset, source_split),
        "language": "en",
        "prompt": prompt.strip(),
        "category": normalize_category(row.get("category")),
        "safety_label": safety_label,
        "strict_benign": strict_benign,
        "include_in_canonical": duplicate_of is None,
        "duplicate_of": duplicate_of,
        "source_dataset_key": source_dataset,
        "source_split": source_split,
        "source_id": source_id,
        "source_file": str(row.get("source_file", "")),
        "source_label": row.get("source_label"),
        "prompt_sha256": str(row.get("prompt_sha256", "")),
        "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
    }


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary_path.replace(path)


def file_metadata(path: Path) -> dict[str, Any]:
    line_count = 0
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for line in handle:
            line_count += 1
            digest.update(line)
    return {
        "path": path.name,
        "rows": line_count,
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def build_views(
    selected_rows: list[dict[str, Any]], exclusion_rows: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    selection_ids = [str(row.get("selection_id", "")) for row in selected_rows]
    if len(selection_ids) != len(set(selection_ids)):
        raise ValueError("Input contains duplicate selection_id values")
    known_ids = set(selection_ids)

    exclusions: dict[str, str] = {}
    for exclusion in exclusion_rows:
        selection_id = str(exclusion.get("selection_id", ""))
        duplicate_of = str(exclusion.get("duplicate_of", ""))
        if selection_id not in known_ids or duplicate_of not in known_ids:
            raise ValueError(
                f"Invalid duplicate exclusion: {selection_id} -> {duplicate_of}"
            )
        if selection_id == duplicate_of:
            raise ValueError(f"Self-referencing duplicate exclusion: {selection_id}")
        exclusions[selection_id] = duplicate_of

    all_rows = [
        convert_row(row, exclusions.get(str(row["selection_id"])))
        for row in selected_rows
    ]
    canonical = [row for row in all_rows if row["include_in_canonical"]]
    strict_benign = [row for row in canonical if row["strict_benign"]]
    evaluation_strict = [
        row for row in strict_benign if row["usage"] == "evaluation"
    ]
    return {
        "all_rows": all_rows,
        "canonical_unique": canonical,
        "strict_benign": strict_benign,
        "evaluation_strict_benign": evaluation_strict,
    }


def main() -> None:
    args = parse_args()
    selected_rows = read_jsonl(args.input)
    exclusion_rows = read_jsonl(args.duplicate_exclusions)
    views = build_views(selected_rows, exclusion_rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    output_files: list[Path] = []
    for view_name, rows in views.items():
        path = args.output_dir / f"{view_name}.jsonl"
        write_jsonl(path, rows)
        output_files.append(path)

    per_dataset_dir = args.output_dir / "datasets"
    datasets = sorted({str(row["dataset"]) for row in views["canonical_unique"]})
    for dataset in datasets:
        path = per_dataset_dir / f"{dataset}.jsonl"
        write_jsonl(
            path,
            (row for row in views["canonical_unique"] if row["dataset"] == dataset),
        )
        output_files.append(path)

    canonical = views["canonical_unique"]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source": portable_path(args.input),
        "duplicate_exclusions": portable_path(args.duplicate_exclusions),
        "excluded_datasets": ["EVOREFUSE"],
        "counts": {name: len(rows) for name, rows in views.items()},
        "canonical_by_dataset": dict(
            sorted(Counter(str(row["dataset"]) for row in canonical).items())
        ),
        "canonical_by_usage": dict(
            sorted(Counter(str(row["usage"]) for row in canonical).items())
        ),
        "strict_benign_by_dataset": dict(
            sorted(
                Counter(
                    str(row["dataset"])
                    for row in views["strict_benign"]
                ).items()
            )
        ),
        "files": [
            file_metadata(path)
            | {"path": path.relative_to(args.output_dir).as_posix()}
            for path in output_files
        ],
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
