#!/usr/bin/env python3
"""Validate translation artifacts and export strict all-language common subsets."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "multilingual_translation_v1.json"
DEFAULT_EXCLUSIONS = REPO_ROOT / "configs" / "multilingual_translation_exclusions_v1.json"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "multilingual-v1" / "final"
JUDGMENT_FIELDS = (
    "semantic_equivalence",
    "task_intent_preserved",
    "referents_preserved",
    "scope_preserved",
    "benign_intent_preserved",
    "category_preserved",
    "harmfulness_introduced",
    "naturalness",
    "decision",
    "strict_keep",
    "reason",
    "deployment",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--exclusions", type=Path, default=DEFAULT_EXCLUSIONS)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--validation-path", type=Path)
    parser.add_argument("--dataset", action="append", default=[])
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


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            encoded = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            handle.write(encoded.decode("utf-8"))
            digest.update(encoded)
            count += 1
    return count, digest.hexdigest()


def unique_by_key(rows: list[dict[str, Any]], path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("record_id", "")), str(row.get("language", "")))
        if not all(key):
            raise ValueError(f"{path}: missing record_id or language")
        if key in result:
            raise ValueError(f"{path}: duplicate key {key}")
        result[key] = row
    return result


def resolve_repo_path(value: str, repo_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def approved_exclusions(path: Path) -> tuple[dict[str, Any], set[tuple[str, str, str]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    keys = {
        (str(row["dataset"]), str(row["record_id"]), str(row["language"]))
        for row in payload["excluded_keys"]
    }
    if len(keys) != len(payload["excluded_keys"]):
        raise ValueError(f"{path}: duplicate excluded key")
    return payload, keys


def compact_judgment(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in JUDGMENT_FIELDS}


def finalize_dataset(
    *,
    spec: dict[str, Any],
    config: dict[str, Any],
    config_path: Path,
    artifact_root: Path,
    output_root: Path,
    approved: set[tuple[str, str, str]],
) -> dict[str, Any]:
    dataset = str(spec["key"])
    source_path = resolve_repo_path(str(spec["path"]), config_path.resolve().parent.parent)
    source_rows = read_jsonl(source_path)
    source_by_id = {str(row["record_id"]): row for row in source_rows}
    if len(source_by_id) != len(source_rows):
        raise ValueError(f"{source_path}: duplicate record_id")

    base = artifact_root / dataset
    translation_path = base / "translations.jsonl"
    judgment_path = base / "translation_judgments.jsonl"
    translations = unique_by_key(read_jsonl(translation_path), translation_path)
    judgments = unique_by_key(read_jsonl(judgment_path), judgment_path)
    languages = [str(language) for language in config["languages"] if language != "en"]
    expected_keys = {(record_id, language) for record_id in source_by_id for language in languages}
    translation_keys = set(translations)
    judgment_keys = set(judgments)
    missing_translations = expected_keys - translation_keys
    unexpected_translations = translation_keys - expected_keys
    missing_judgments = expected_keys - judgment_keys
    unexpected_judgments = judgment_keys - expected_keys
    approved_for_dataset = {
        (record_id, language)
        for excluded_dataset, record_id, language in approved
        if excluded_dataset == dataset
    }
    if missing_translations or unexpected_translations:
        raise ValueError(
            f"{dataset}: translation key mismatch; missing={len(missing_translations)} "
            f"unexpected={len(unexpected_translations)}"
        )
    if missing_judgments != approved_for_dataset or unexpected_judgments:
        raise ValueError(
            f"{dataset}: judgment exclusions do not match approved keys; "
            f"missing={sorted(missing_judgments)} approved={sorted(approved_for_dataset)} "
            f"unexpected={sorted(unexpected_judgments)}"
        )

    strict_keep_keys = {key for key, row in judgments.items() if row.get("strict_keep") is True}
    decision_counts = Counter(
        "keep" if row.get("strict_keep") is True else str(row.get("decision", "unknown"))
        for row in judgments.values()
    )
    strict_keep_by_language = {
        language: sum((record_id, language) in strict_keep_keys for record_id in source_by_id)
        for language in languages
    }
    common_ids = [
        record_id
        for record_id in source_by_id
        if all((record_id, language) in strict_keep_keys for language in languages)
    ]

    def output_rows() -> Iterable[dict[str, Any]]:
        for record_id in common_ids:
            source = source_by_id[record_id]
            prompts = {"en": source["prompt"]}
            quality: dict[str, Any] = {}
            for language in languages:
                key = (record_id, language)
                translation = translations[key]
                if translation.get("source_prompt_sha256") != source.get("prompt_sha256"):
                    raise ValueError(f"{dataset}:{key}: source prompt SHA256 mismatch")
                prompts[language] = translation["translated_prompt"]
                quality[language] = compact_judgment(judgments[key])
            yield {
                "multilingual_schema_version": "multilingual-common-v1",
                "prompt_version": config["prompt_version"],
                **source,
                "languages": ["en", *languages],
                "prompts": prompts,
                "translation_quality": quality,
            }

    output_path = output_root / "datasets" / f"{dataset}.jsonl"
    output_count, output_sha256 = write_jsonl(output_path, output_rows())
    if output_count != len(common_ids):
        raise AssertionError(f"{dataset}: output count changed while writing")
    return {
        "dataset": dataset,
        "source_path": portable_path(source_path),
        "artifact_path": portable_path(base),
        "output_path": portable_path(output_path),
        "source_rows": len(source_rows),
        "expected_pairs": len(expected_keys),
        "translation_pairs": len(translation_keys),
        "judgment_pairs": len(judgment_keys),
        "approved_missing_judgments": [
            {"record_id": record_id, "language": language}
            for record_id, language in sorted(missing_judgments)
        ],
        "strict_keep_pairs": len(strict_keep_keys),
        "decision_counts": dict(sorted(decision_counts.items())),
        "strict_keep_by_language": strict_keep_by_language,
        "strict_common_rows": output_count,
        "excluded_source_rows": len(source_rows) - output_count,
        "common_usage": dict(sorted(Counter(source_by_id[row_id]["usage"] for row_id in common_ids).items())),
        "common_strict_benign": dict(
            sorted(Counter(str(source_by_id[row_id]["strict_benign"]).lower() for row_id in common_ids).items())
        ),
        "output_sha256": output_sha256,
        "complete_with_approved_exclusions": True,
    }


def main() -> None:
    args = parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    exclusions_payload, approved = approved_exclusions(args.exclusions)
    artifact_root = args.artifact_root or (
        resolve_repo_path(str(config["artifact_root"]), args.config.resolve().parent.parent) / "full"
    )
    selected = set(args.dataset)
    specs = [
        spec
        for spec in config["datasets"]
        if (not selected or str(spec["key"]) in selected)
        and (artifact_root / str(spec["key"]) / "translations.jsonl").exists()
    ]
    known = {str(spec["key"]) for spec in config["datasets"]}
    if selected - known:
        raise ValueError(f"unknown datasets: {sorted(selected - known)}")
    reports = [
        finalize_dataset(
            spec=spec,
            config=config,
            config_path=args.config,
            artifact_root=artifact_root,
            output_root=args.output_root,
            approved=approved,
        )
        for spec in specs
    ]
    used_exclusions = {
        (report["dataset"], item["record_id"], item["language"])
        for report in reports
        for item in report["approved_missing_judgments"]
    }
    relevant_approved = {key for key in approved if key[0] in {report["dataset"] for report in reports}}
    if used_exclusions != relevant_approved:
        raise ValueError("approved exclusion list contains keys not observed as missing")
    manifest = {
        "schema_version": "multilingual-final-manifest-v1",
        "config": portable_path(args.config),
        "exclusions": portable_path(args.exclusions),
        "exclusion_decision": exclusions_payload,
        "artifact_root": portable_path(artifact_root),
        "languages": config["languages"],
        "selection_rule": "retain a source record only when all eight target-language judgments have strict_keep=true",
        "datasets": reports,
        "totals": {
            "source_rows": sum(report["source_rows"] for report in reports),
            "translation_pairs": sum(report["translation_pairs"] for report in reports),
            "judgment_pairs": sum(report["judgment_pairs"] for report in reports),
            "approved_missing_judgments": sum(len(report["approved_missing_judgments"]) for report in reports),
            "strict_keep_pairs": sum(report["strict_keep_pairs"] for report in reports),
            "strict_common_rows": sum(report["strict_common_rows"] for report in reports),
        },
        "complete_with_approved_exclusions": all(
            report["complete_with_approved_exclusions"] for report in reports
        ),
    }
    write_json(args.output_root / "manifest.json", manifest)
    validation_path = args.validation_path or args.output_root.parent / "prior-five-validation.json"
    write_json(validation_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
