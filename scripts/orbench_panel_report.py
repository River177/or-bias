#!/usr/bin/env python3
"""Build result-only panel tables after every configured model is complete."""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import orbench_multimodel as multimodel

ROOT = Path(__file__).resolve().parents[1]
TOPIC_ZH = {
    "illegal": "非法",
    "privacy": "隐私",
    "unethical": "不道德",
    "harmful": "有害",
    "deception": "欺骗",
    "sexual": "性相关",
    "violence": "暴力",
    "self-harm": "自我伤害",
    "hate": "仇恨",
    "harassment": "骚扰",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_csv(path: Path, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def rate_cell(k: int, n: int) -> str:
    return f"{100 * k / n:.2f}% (n={n})" if n else "NA"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "orbench_multilingual_v2.yaml")
    parser.add_argument("--expected-per-model", type=int, default=6336)
    parser.add_argument(
        "--models",
        default=None,
        help="Optional comma-separated configured model keys to include",
    )
    parser.add_argument("--wait-pids", default=None, help="Comma-separated stage-runner PIDs to wait for")
    args = parser.parse_args()

    wait_pids = [int(value) for value in args.wait_pids.split(",")] if args.wait_pids else []
    while wait_pids:
        active = []
        for pid in wait_pids:
            try:
                os.kill(pid, 0)
                active.append(pid)
            except (ProcessLookupError, PermissionError):
                pass
        if not active:
            break
        print(json.dumps({"waiting_for_pids": active}), flush=True)
        wait_pids = active
        time.sleep(60)

    config = args.config.resolve()
    cfg = multimodel.load_simple_yaml(config)
    experiment_dir = Path(str(cfg.get("experiment_dir", "experiments/v2")))
    if not experiment_dir.is_absolute():
        experiment_dir = ROOT / experiment_dir
    output_dir = experiment_dir / "summary"
    configured_models = [model["key"] for model in cfg["models"]]
    if args.models:
        requested = [value.strip() for value in args.models.split(",") if value.strip()]
        unknown = set(requested) - set(configured_models)
        if unknown:
            raise SystemExit(f"Unknown configured model keys: {','.join(sorted(unknown))}")
        models = [model for model in configured_models if model in requested]
        if len(models) != len(requested):
            raise SystemExit("--models must not contain duplicate model keys")
    else:
        models = configured_models
    pending_models = [model for model in configured_models if model not in models]
    languages = list(cfg["languages"])

    panel: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    completion_rows = []
    for model_key in models:
        model_dir = experiment_dir / "models" / model_key
        generations = {
            row["generation_id"]: row
            for row in read_jsonl(model_dir / "generations.jsonl")
            if row.get("generation_id") and not row.get("generation_error")
        }
        judgments = {
            row["generation_id"]: row
            for row in read_jsonl(model_dir / "response_judgments.jsonl")
            if row.get("generation_id") and not row.get("judge_error")
        }
        for refusal in read_jsonl(model_dir / "policy_blocked_refusals.jsonl"):
            if not refusal.get("policy_blocked") or not refusal.get("refusal"):
                continue
            generation_id = refusal["generation_id"]
            if generation_id in generations:
                continue
            generations[generation_id] = {
                "generation_id": generation_id,
                "prompt_id": refusal["prompt_id"],
                "language": refusal["language"],
                "category": refusal["category"],
                "sample_idx": int(refusal.get("sample_idx", 0)),
                "policy_blocked": True,
            }
            judgments[generation_id] = refusal
        pairs = [(generation, judgments[generation_id]) for generation_id, generation in generations.items() if generation_id in judgments]
        completion_rows.append({
            "model_key": model_key,
            "n_generation": len(generations),
            "n_valid_judge": len(pairs),
            "n_generation_missing": args.expected_per_model - len(generations),
            "n_judge_missing": args.expected_per_model - len(pairs),
        })
        if len(generations) != args.expected_per_model or len(pairs) != args.expected_per_model:
            raise RuntimeError(f"{model_key} incomplete: generation={len(generations)}, valid_judge={len(pairs)}")
        panel[model_key] = pairs

    language_rows = []
    topic_language_rows = []
    resource_rows = []
    resource_groups = {
        "high": list(cfg["high_resource_languages"]),
        "medium": list(cfg["medium_resource_languages"]),
        "low": list(cfg["low_resource_languages"]),
    }
    for model_key in models:
        pairs = panel[model_key]
        for language in languages:
            subset = [judgment for generation, judgment in pairs if generation["language"] == language]
            refusal = sum(bool(row.get("refusal")) for row in subset)
            language_rows.append({"language": language, "model_key": model_key, "refusal_count": refusal, "n_valid": len(subset), "refusal_rate": refusal / len(subset)})
        for resource_group, group_languages in resource_groups.items():
            subset = [judgment for generation, judgment in pairs if generation["language"] in group_languages]
            refusal = sum(bool(row.get("refusal")) for row in subset)
            resource_rows.append({"resource_group": resource_group, "model_key": model_key, "refusal_count": refusal, "n_valid": len(subset), "refusal_rate": refusal / len(subset)})
        for topic in TOPIC_ZH:
            for language in languages:
                subset = [judgment for generation, judgment in pairs if generation["category"] == topic and generation["language"] == language]
                refusal = sum(bool(row.get("refusal")) for row in subset)
                topic_language_rows.append({"topic": topic, "topic_zh": TOPIC_ZH[topic], "language": language, "model_key": model_key, "refusal_count": refusal, "n_valid": len(subset), "refusal_rate": refusal / len(subset)})

    write_csv(output_dir / "completion_status.csv", list(completion_rows[0]), completion_rows)
    write_csv(output_dir / "language_refusal_rates.csv", list(language_rows[0]), language_rows)
    write_csv(output_dir / "resource_group_refusal_rates.csv", list(resource_rows[0]), resource_rows)
    write_csv(output_dir / "topic_language_refusal_rates.csv", list(topic_language_rows[0]), topic_language_rows)

    language_lookup = {(row["language"], row["model_key"]): row for row in language_rows}
    resource_lookup = {(row["resource_group"], row["model_key"]): row for row in resource_rows}
    topic_lookup = {(row["topic"], row["language"], row["model_key"]): row for row in topic_language_rows}
    lines = [
        "# OR-Bench v2 多语言 over-refusal 实验结果",
        "",
        f"- 模型数：{len(models)}",
        f"- 每模型：{args.expected_per_model:,} 条 response，{args.expected_per_model:,} 条有效 response judge",
        f"- 总计：{len(models) * args.expected_per_model:,} 条 response，{len(models) * args.expected_per_model:,} 条有效 response judge",
        f"- Response judge：`{cfg['response_judge_deployment']}`",
        "- `gpt-5.5` 为 self-judge 条件。",
        "- 网关 `cyber_policy` 拦截按 refusal 计入；记录保留 `policy_blocked=true`，不伪造 response。",
        *([f"- 尚未纳入：`{', '.join(pending_models)}`（结果未完成）。"] if pending_models else []),
        "",
        "## 各语言 refusal rate",
        "",
        "| Language | " + " | ".join(models) + " |",
        "|---|" + "---:|" * len(models),
    ]
    for language in languages:
        cells = [rate_cell(language_lookup[(language, model)]["refusal_count"], language_lookup[(language, model)]["n_valid"]) for model in models]
        lines.append("| " + language + " | " + " | ".join(cells) + " |")
    lines += ["", "## Resource group refusal rate", "", "| Resource | " + " | ".join(models) + " |", "|---|" + "---:|" * len(models)]
    for resource_group in ("high", "medium", "low"):
        cells = [rate_cell(resource_lookup[(resource_group, model)]["refusal_count"], resource_lookup[(resource_group, model)]["n_valid"]) for model in models]
        lines.append("| " + resource_group + " | " + " | ".join(cells) + " |")
    lines += ["", "## Topic × language × model refusal rate", ""]
    for topic, topic_zh in TOPIC_ZH.items():
        lines += [f"### {topic}（{topic_zh}）", "", "| Language | " + " | ".join(models) + " |", "|---|" + "---:|" * len(models)]
        for language in languages:
            cells = []
            for model in models:
                row = topic_lookup[(topic, language, model)]
                cells.append(f"{row['refusal_count']}/{row['n_valid']} ({100 * row['refusal_rate']:.2f}%)")
            lines.append("| " + language + " | " + " | ".join(cells) + " |")
        lines.append("")
    (output_dir / "results-v2.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"status": "complete", "models": len(models), "output_dir": str(output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
