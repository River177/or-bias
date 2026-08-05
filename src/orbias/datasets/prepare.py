#!/usr/bin/env python3
"""Build the fixed external over-refusal selection without modifying raw data.

The output preserves every source row selected by policy. Exact duplicate prompts
within a dataset remain present but are marked so evaluation code can use an
unbiased unique denominator without losing source provenance.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from orbias.paths import REPO_ROOT

DEFAULT_CONFIG = REPO_ROOT / "configs" / "datasets" / "external-overrefusal-v1.json"
DEFAULT_INPUT_ROOT = REPO_ROOT.parent / "or-bias-artifacts" / "raw" / "external-overrefusal"
DEFAULT_OUTPUT_DIR = DEFAULT_INPUT_ROOT / "selected"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare the fixed source-preserving external over-refusal selection."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def read_rows(path: Path, file_format: str) -> Iterable[tuple[int, dict[str, Any]]]:
    if file_format == "csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row_number, row in enumerate(csv.DictReader(handle), start=1):
                yield row_number, dict(row)
        return
    if file_format == "jsonl":
        with path.open(encoding="utf-8") as handle:
            for row_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"{path}:{row_number} is not a JSON object")
                yield row_number, value
        return
    raise ValueError(f"Unsupported format: {file_format}")


def matches_filters(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    return all(row.get(field) == expected for field, expected in filters.items())


def clean_prompt(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing prompt at {location}")
    return value.strip()


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def build_dataset(
    spec: dict[str, Any], input_root: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = str(spec["key"])
    selected: list[dict[str, Any]] = []
    seen_prompt: dict[str, str] = {}
    seen_selection_ids: set[str] = set()
    source_rows = 0

    for file_spec in spec["files"]:
        relative_path = Path(file_spec["path"])
        source_path = input_root / relative_path
        split = str(file_spec["split"])
        if not source_path.is_file():
            raise FileNotFoundError(source_path)

        for row_number, row in read_rows(source_path, str(spec["format"])):
            source_rows += 1
            if not matches_filters(row, dict(spec.get("filters", {}))):
                continue

            prompt = clean_prompt(
                row.get(spec["prompt_field"]), f"{relative_path}:{row_number}"
            )
            source_id_value = (
                row.get(spec["id_field"])
                if spec.get("id_field")
                else f"row-{row_number:06d}"
            )
            if source_id_value is None or str(source_id_value).strip() == "":
                raise ValueError(f"Missing source id at {relative_path}:{row_number}")
            source_id = str(source_id_value)
            selection_id = f"{key}:{split}:{source_id}"
            if selection_id in seen_selection_ids:
                raise ValueError(f"Duplicate selection_id: {selection_id}")
            seen_selection_ids.add(selection_id)

            if spec.get("source_label_field"):
                source_label = row.get(spec["source_label_field"])
            else:
                source_label = spec.get("source_label_constant")
            source_label = None if source_label is None else str(source_label)
            ambiguous_labels = {str(x) for x in spec.get("ambiguous_labels", [])}

            digest = prompt_hash(prompt)
            duplicate_of = seen_prompt.get(digest)
            include_unique = duplicate_of is None
            if include_unique:
                seen_prompt[digest] = selection_id

            metadata = {
                field: row.get(field) for field in spec.get("metadata_fields", [])
            }
            category_field = spec.get("category_field")
            category = row.get(category_field) if category_field else None

            selected.append(
                {
                    "selection_id": selection_id,
                    "dataset": key,
                    "source_split": split,
                    "source_file": relative_path.as_posix(),
                    "source_id": source_id,
                    "prompt": prompt,
                    "prompt_sha256": digest,
                    "source_label": source_label,
                    "category": category,
                    "evaluation_role": (
                        "ambiguous" if source_label in ambiguous_labels else "benign"
                    ),
                    "primary_benign_eligible": source_label not in ambiguous_labels,
                    "include_in_unique_evaluation": include_unique,
                    "duplicate_of": duplicate_of,
                    "metadata": metadata,
                }
            )

    expected = int(spec["expected_selected_rows"])
    if len(selected) != expected:
        raise RuntimeError(f"{key}: selected {len(selected)} rows, expected {expected}")

    manifest = {
        "dataset": key,
        "source_rows_read": source_rows,
        "selected_rows": len(selected),
        "exact_unique_prompts": sum(
            bool(row["include_in_unique_evaluation"]) for row in selected
        ),
        "duplicate_rows": sum(
            not bool(row["include_in_unique_evaluation"]) for row in selected
        ),
        "primary_benign_rows": sum(
            bool(row["primary_benign_eligible"]) for row in selected
        ),
        "primary_benign_unique_prompts": sum(
            bool(row["primary_benign_eligible"])
            and bool(row["include_in_unique_evaluation"])
            for row in selected
        ),
        "by_split": dict(Counter(str(row["source_split"]) for row in selected)),
        "by_source_label": dict(
            Counter(str(row["source_label"]) for row in selected)
        ),
        "by_evaluation_role": dict(
            Counter(str(row["evaluation_role"]) for row in selected)
        ),
    }
    return selected, manifest


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    args = parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict[str, Any]] = []
    datasets_manifest: list[dict[str, Any]] = []
    for spec in config["datasets"]:
        rows, manifest = build_dataset(spec, args.input_root)
        output_path = args.output_dir / f"{spec['key']}.jsonl"
        write_jsonl(output_path, rows)
        manifest["output_file"] = output_path.name
        datasets_manifest.append(manifest)
        all_rows.extend(rows)

    write_jsonl(args.output_dir / "all_selected.jsonl", all_rows)
    summary = {
        "selection_version": config["version"],
        "config": portable_path(args.config),
        "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
        "input_root": portable_path(args.input_root),
        "datasets": datasets_manifest,
        "totals": {
            "selected_rows": len(all_rows),
            "exact_unique_within_dataset": sum(
                bool(row["include_in_unique_evaluation"]) for row in all_rows
            ),
            "primary_benign_rows": sum(
                bool(row["primary_benign_eligible"]) for row in all_rows
            ),
            "primary_benign_unique_within_dataset": sum(
                bool(row["primary_benign_eligible"])
                and bool(row["include_in_unique_evaluation"])
                for row in all_rows
            ),
        },
    }
    (args.output_dir / "selection_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
