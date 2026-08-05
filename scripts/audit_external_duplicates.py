#!/usr/bin/env python3
"""Audit duplicate prompts in the fixed external over-refusal selection.

This script is read-only with respect to selected datasets. It distinguishes
within-dataset duplicates from cross-dataset overlap and treats fuzzy matches as
review candidates rather than automatically deleting them.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / "data" / "external" / "selected" / "all_selected.jsonl"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "external" / "duplicate_audit"
WORD_RE = re.compile(r"[a-z0-9]+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit internal and cross-dataset duplicate prompts."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("prompt"), str):
                raise ValueError(f"Invalid prompt row at {path}:{line_number}")
            rows.append(row)
    return rows


def canonical_text(text: str) -> str:
    """Normalize case, Unicode variants, punctuation, and whitespace."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(
        " " if unicodedata.category(char)[0] in {"P", "Z", "C"} else char
        for char in text
    )
    return " ".join(text.split())


def words(text: str) -> tuple[str, ...]:
    return tuple(WORD_RE.findall(unicodedata.normalize("NFKC", text).casefold()))


def token_bag(text: str) -> str:
    """Order-insensitive form used only to find high-confidence review groups."""
    return " ".join(sorted(words(text)))


def duplicate_groups(
    rows: list[dict[str, Any]], key_fn: Callable[[str], str]
) -> list[list[int]]:
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[(str(row["dataset"]), key_fn(row["prompt"]))].append(index)
    return [indices for indices in grouped.values() if len(indices) > 1]


def cross_exact_groups(
    rows: list[dict[str, Any]], key_fn: Callable[[str], str]
) -> list[list[int]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[key_fn(row["prompt"])].append(index)
    return [
        indices
        for indices in grouped.values()
        if len(indices) > 1
        and len({str(rows[index]["dataset"]) for index in indices}) > 1
    ]


def row_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "selection_id": row["selection_id"],
        "dataset": row["dataset"],
        "source_split": row["source_split"],
        "source_id": row["source_id"],
        "prompt": row["prompt"],
    }


def serialize_groups(
    rows: list[dict[str, Any]], groups: Iterable[list[int]], match_type: str
) -> list[dict[str, Any]]:
    return [
        {
            "match_type": match_type,
            "rows": [row_ref(rows[index]) for index in indices],
        }
        for indices in groups
    ]


def fuzzy_cross_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find lexical cross-dataset candidates using shared word 4-grams.

    The thresholds are intentionally permissive. Returned pairs require manual
    semantic review and are not counted as confirmed duplicates.
    """
    tokenized = [words(row["prompt"]) for row in rows]
    postings: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for index, tokens in enumerate(tokenized):
        for ngram in set(zip(tokens, tokens[1:], tokens[2:], tokens[3:])):
            postings[ngram].append(index)

    pair_hits: Counter[tuple[int, int]] = Counter()
    for indices in postings.values():
        if len(indices) > 80:
            continue
        by_dataset: dict[str, list[int]] = defaultdict(list)
        for index in indices:
            by_dataset[str(rows[index]["dataset"])].append(index)
        datasets = sorted(by_dataset)
        for left_position, left_dataset in enumerate(datasets):
            for right_dataset in datasets[left_position + 1 :]:
                for left in by_dataset[left_dataset]:
                    for right in by_dataset[right_dataset]:
                        pair_hits[(min(left, right), max(left, right))] += 1

    candidates: list[dict[str, Any]] = []
    for (left, right), shared_ngrams in pair_hits.items():
        left_tokens = tokenized[left]
        right_tokens = tokenized[right]
        left_set = set(left_tokens)
        right_set = set(right_tokens)
        union = left_set | right_set
        intersection = left_set & right_set
        jaccard = len(intersection) / len(union) if union else 0.0
        containment = (
            len(intersection) / min(len(left_set), len(right_set))
            if left_set and right_set
            else 0.0
        )
        sequence_ratio = SequenceMatcher(
            None, left_tokens, right_tokens, autojunk=False
        ).ratio()
        if not (
            jaccard >= 0.60
            or (containment >= 0.78 and sequence_ratio >= 0.60)
            or sequence_ratio >= 0.78
        ):
            continue
        candidates.append(
            {
                "left": row_ref(rows[left]),
                "right": row_ref(rows[right]),
                "token_jaccard": round(jaccard, 6),
                "token_containment": round(containment, 6),
                "sequence_ratio": round(sequence_ratio, 6),
                "shared_word_4grams": shared_ngrams,
                "status": "manual_review_candidate",
            }
        )

    candidates.sort(
        key=lambda item: (
            -max(item["token_jaccard"], item["sequence_ratio"]),
            item["left"]["selection_id"],
            item["right"]["selection_id"],
        )
    )
    return candidates


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    args = parse_args()
    rows = read_jsonl(args.input)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    raw_groups = duplicate_groups(rows, lambda text: text)
    canonical_groups = duplicate_groups(rows, canonical_text)
    token_bag_groups = duplicate_groups(rows, token_bag)
    cross_raw = cross_exact_groups(rows, lambda text: text)
    cross_canonical = cross_exact_groups(rows, canonical_text)
    cross_token_bag = cross_exact_groups(rows, token_bag)
    fuzzy_candidates = fuzzy_cross_candidates(rows)

    internal_groups = serialize_groups(rows, raw_groups, "raw_exact")
    included_group_ids = {
        frozenset(row["selection_id"] for row in group["rows"])
        for group in internal_groups
    }
    for indices, match_type in (
        (canonical_groups, "canonical_exact"),
        (token_bag_groups, "token_order_variant"),
    ):
        for group in serialize_groups(rows, indices, match_type):
            group_ids = frozenset(row["selection_id"] for row in group["rows"])
            if group_ids not in included_group_ids:
                internal_groups.append(group)
                included_group_ids.add(group_ids)
    write_jsonl(args.output_dir / "internal_duplicate_groups.jsonl", internal_groups)
    write_jsonl(args.output_dir / "cross_dataset_candidates.jsonl", fuzzy_candidates)

    exclusion_rows: list[dict[str, Any]] = []
    excluded_ids: set[str] = set()
    for group in token_bag_groups:
        retained = rows[group[0]]
        for index in group[1:]:
            excluded = rows[index]
            selection_id = str(excluded["selection_id"])
            if selection_id in excluded_ids:
                continue
            excluded_ids.add(selection_id)
            exclusion_rows.append(
                {
                    **row_ref(excluded),
                    "duplicate_of": retained["selection_id"],
                    "reason": "reviewed_internal_duplicate",
                }
            )
    exclusion_rows.sort(key=lambda row: row["selection_id"])
    write_jsonl(
        args.output_dir / "internal_duplicate_exclusions.jsonl", exclusion_rows
    )

    with (args.output_dir / "cross_dataset_candidates.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "left_id",
                "left_dataset",
                "left_prompt",
                "right_id",
                "right_dataset",
                "right_prompt",
                "token_jaccard",
                "token_containment",
                "sequence_ratio",
                "shared_word_4grams",
                "status",
            ],
        )
        writer.writeheader()
        for candidate in fuzzy_candidates:
            writer.writerow(
                {
                    "left_id": candidate["left"]["selection_id"],
                    "left_dataset": candidate["left"]["dataset"],
                    "left_prompt": candidate["left"]["prompt"],
                    "right_id": candidate["right"]["selection_id"],
                    "right_dataset": candidate["right"]["dataset"],
                    "right_prompt": candidate["right"]["prompt"],
                    "token_jaccard": candidate["token_jaccard"],
                    "token_containment": candidate["token_containment"],
                    "sequence_ratio": candidate["sequence_ratio"],
                    "shared_word_4grams": candidate["shared_word_4grams"],
                    "status": candidate["status"],
                }
            )

    by_dataset: dict[str, dict[str, int]] = {}
    datasets = sorted({str(row["dataset"]) for row in rows})
    for dataset in datasets:
        dataset_rows = [row for row in rows if row["dataset"] == dataset]
        by_dataset[dataset] = {
            "selected_rows": len(dataset_rows),
            "raw_exact_duplicate_rows": len(dataset_rows)
            - len({row["prompt"] for row in dataset_rows}),
            "canonical_duplicate_rows": len(dataset_rows)
            - len({canonical_text(row["prompt"]) for row in dataset_rows}),
            "token_bag_duplicate_rows": len(dataset_rows)
            - len({token_bag(row["prompt"]) for row in dataset_rows}),
        }

    summary = {
        "input": str(args.input.resolve()),
        "rows": len(rows),
        "datasets": len(datasets),
        "excluded_datasets": ["EVOREFUSE"],
        "by_dataset": by_dataset,
        "cross_dataset": {
            "raw_exact_groups": len(cross_raw),
            "canonical_exact_groups": len(cross_canonical),
            "token_bag_groups": len(cross_token_bag),
            "lexical_review_candidates": len(fuzzy_candidates),
            "confirmed_duplicates": 0,
        },
        "global_unique_rows_after_reviewed_internal_dedup": len(rows)
        - len(exclusion_rows),
        "reviewed_internal_exclusions": len(exclusion_rows),
        "notes": [
            "Token-bag groups must be inspected before removal because word order can change meaning.",
            "Lexical cross-dataset candidates are not semantic duplicate judgments.",
            "EVOREFUSE is not present in the fixed selected panel and was not audited here.",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
