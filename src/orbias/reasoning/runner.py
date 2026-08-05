#!/usr/bin/env python3
"""Independent, resumable OR-Bench reasoning-effort experiment runner."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from orbias.core import ArtifactStore
from orbias.evaluation.multimodel import (
    AdaptiveConcurrency,
    RateLimiter,
    orbench_response_judge_prompt,
    parse_orbench_classification,
)
from orbias.paths import REPO_ROOT, artifact_path

ROOT = REPO_ROOT
DEFAULT_CONFIG = ROOT / "configs" / "experiments" / "reasoning-v2.json"

_CLIENTS: dict[tuple[str, str, int], Any] = {}
_CLIENT_LOCK = threading.Lock()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return ArtifactStore(path.parent).read_jsonl(path.name)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    ArtifactStore(path.parent).append(path.name, row)


def resolve(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def resolve_output(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else artifact_path(candidate)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_client(cfg: dict[str, Any]):
    key = (str(cfg["instance"]), str(cfg["api_version"]), int(cfg["call_timeout_seconds"]))
    with _CLIENT_LOCK:
        if key not in _CLIENTS:
            from azure.identity import AzureCliCredential, ChainedTokenCredential, ManagedIdentityCredential, get_bearer_token_provider
            from openai import AzureOpenAI

            provider = get_bearer_token_provider(
                ChainedTokenCredential(AzureCliCredential(), ManagedIdentityCredential()),
                "api://trapi/.default",
            )
            _CLIENTS[key] = AzureOpenAI(
                azure_endpoint=f"https://trapi.research.microsoft.com/{cfg['instance']}",
                azure_ad_token_provider=provider,
                api_version=str(cfg["api_version"]),
                timeout=float(cfg["call_timeout_seconds"]),
                max_retries=0,
            )
    return _CLIENTS[key]


def error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def is_policy_block(error: str) -> bool:
    lowered = error.lower()
    markers = ("responsibleaipolicyviolation", "content_filter", "content management policy", "cyber_policy", "invalid_prompt")
    return any(marker in lowered for marker in markers)


def is_retryable(error: str) -> bool:
    lowered = error.lower()
    markers = ("429", "ratelimit", "timeout", "connection", "status code: 500", "status code: 502", "status code: 503", "status code: 504")
    return any(marker in lowered for marker in markers)


def call_chat(
    cfg: dict[str, Any],
    deployment: str,
    system_prompt: str,
    user_prompt: str,
    reasoning_effort: str | None,
) -> tuple[str, dict[str, Any]]:
    last_error = ""
    for attempt in range(int(cfg["api_retry_count"]) + 1):
        try:
            kwargs: dict[str, Any] = {
                "model": deployment,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            if reasoning_effort is not None:
                kwargs["reasoning_effort"] = reasoning_effort
            response = get_client(cfg).chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            if not content:
                raise RuntimeError("TRAPI returned an empty completion")
            metadata = response.model_dump() if hasattr(response, "model_dump") else {"content": content}
            return content, metadata
        except Exception as exc:
            last_error = error_text(exc)
            if is_policy_block(last_error):
                raise RuntimeError(last_error) from exc
            if attempt >= int(cfg["api_retry_count"]) or not is_retryable(last_error):
                raise RuntimeError(last_error) from exc
            if "429" in last_error or "ratelimit" in last_error.lower():
                delay = 60 * (2**attempt) + random.uniform(0, 10)
            elif "connection" in last_error.lower() or "timeout" in last_error.lower():
                delay = 15 * (attempt + 1) + random.uniform(0, 5)
            else:
                delay = 2**attempt + random.uniform(0, 2)
            time.sleep(delay)
    raise RuntimeError(last_error)


def condition_dir(cfg: dict[str, Any], key: str) -> Path:
    return resolve_output(cfg["output_dir"]) / "conditions" / key


def concurrency_path(cfg: dict[str, Any], group: str) -> Path:
    return resolve_output(cfg["output_dir"]) / "concurrency" / f"{group}.json"


def load_concurrency_state(cfg: dict[str, Any], group: str) -> dict[str, Any] | None:
    path = concurrency_path(cfg, group)
    if not path.exists():
        return None
    try:
        state = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return state if state.get("algorithm") == "tcp-reno-aimd-v1" else None


def save_concurrency_state(cfg: dict[str, Any], group: str, state: dict[str, Any]) -> None:
    path = concurrency_path(cfg, group)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def row_key(row: dict[str, Any]) -> tuple[str, str, int]:
    return row["prompt_id"], row["language"], int(row.get("sample_idx", 0))


def generation_id(condition_key: str, row: dict[str, Any]) -> str:
    prompt_id, language, sample_idx = row_key(row)
    return f"{condition_key}:{prompt_id}:{language}:{sample_idx}"


def policy_rows(cfg: dict[str, Any], condition_key: str) -> list[dict[str, Any]]:
    return [
        row
        for row in read_jsonl(condition_dir(cfg, condition_key) / "policy_blocked_refusals.jsonl")
        if row.get("policy_blocked") and row.get("refusal")
    ]


def completed_generation_keys(cfg: dict[str, Any], condition_key: str) -> set[tuple[str, str, int]]:
    rows = read_jsonl(condition_dir(cfg, condition_key) / "generations.jsonl")
    keys = {row_key(row) for row in rows if not row.get("generation_error") and row.get("response")}
    keys.update(row_key(row) for row in policy_rows(cfg, condition_key))
    return keys


def completed_judgment_ids(cfg: dict[str, Any], condition_key: str) -> set[str]:
    rows = read_jsonl(condition_dir(cfg, condition_key) / "response_judgments.jsonl")
    keys = {str(row["generation_id"]) for row in rows if row.get("generation_id") and not row.get("judge_error")}
    keys.update(str(row["generation_id"]) for row in policy_rows(cfg, condition_key))
    return keys


def preflight(cfg: dict[str, Any]) -> dict[str, Any]:
    frozen = resolve(cfg["frozen_dataset"])
    full = read_jsonl(resolve(cfg["full_manifest"]))
    smoke = read_jsonl(resolve(cfg["smoke_manifest"]))
    frozen_rows = read_jsonl(frozen)
    observed_hash = sha256(frozen)
    if observed_hash != cfg["expected_dataset_sha256"]:
        raise RuntimeError(f"Frozen dataset SHA256 mismatch: {observed_hash}")
    if len(frozen_rows) != 6336 or len(full) != 6336 or len(smoke) != 90:
        raise RuntimeError(f"Unexpected row counts frozen={len(frozen_rows)} full={len(full)} smoke={len(smoke)}")
    expected_languages = set(cfg["languages"])
    if {row["language"] for row in full} != expected_languages:
        raise RuntimeError("Full manifest language set mismatch")
    smoke_languages = Counter(row["language"] for row in smoke)
    smoke_categories = Counter(row["category"] for row in smoke)
    if any(smoke_languages[language] != 10 for language in expected_languages):
        raise RuntimeError(f"Smoke language coverage mismatch: {smoke_languages}")
    if len(smoke_categories) != 10 or any(count != 9 for count in smoke_categories.values()):
        raise RuntimeError(f"Smoke category coverage mismatch: {smoke_categories}")
    condition_keys = [row["key"] for row in cfg["conditions"]]
    if len(condition_keys) != 9 or len(set(condition_keys)) != 9:
        raise RuntimeError("Reasoning config must contain nine unique new conditions")
    for condition in cfg["conditions"]:
        if condition["family"].startswith("gpt-") and condition["effort"] not in {"none", "medium", "high"}:
            raise RuntimeError(f"Invalid GPT effort: {condition}")
    result = {
        "status": "ok",
        "frozen_rows": len(frozen_rows),
        "full_rows": len(full),
        "smoke_rows": len(smoke),
        "sha256": observed_hash,
        "conditions": condition_keys,
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def build_controllers(cfg: dict[str, Any]) -> tuple[dict[str, AdaptiveConcurrency], dict[str, RateLimiter]]:
    groups = {condition["concurrency_group"] for condition in cfg["conditions"]}
    controllers = {
        group: AdaptiveConcurrency(
            int(cfg["initial_workers_per_deployment"]),
            max_concurrency=int(cfg["max_workers_per_deployment"]),
            state=load_concurrency_state(cfg, group),
        )
        for group in groups
    }
    return controllers, {group: RateLimiter(0) for group in groups}


def run_generation_round(cfg: dict[str, Any], manifest: list[dict[str, Any]], cohort: str) -> int:
    controllers, limiters = build_controllers(cfg)
    conditions = {condition["key"]: condition for condition in cfg["conditions"]}
    locks = {key: threading.Lock() for key in conditions}
    completed = {key: completed_generation_keys(cfg, key) for key in conditions}
    tasks = [
        (condition, row)
        for row in manifest
        for condition in cfg["conditions"]
        if row_key(row) not in completed[condition["key"]]
    ]

    def worker(condition: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
        key = condition["key"]
        group = condition["concurrency_group"]
        controller = controllers[group]
        controller.acquire()
        try:
            limiters[group].wait()
            try:
                effort = condition["effort"] if condition["family"].startswith("gpt-") else None
                response, metadata = call_chat(
                    cfg,
                    condition["deployment"],
                    cfg["system_prompt"],
                    row["prompt"],
                    effort,
                )
                return {
                    "condition_key": key,
                    "family": condition["family"],
                    "effort": condition["effort"],
                    "target_deployment": condition["deployment"],
                    "generation_id": generation_id(key, row),
                    "prompt_id": row["prompt_id"],
                    "category": row["category"],
                    "language": row["language"],
                    "prompt": row["prompt"],
                    "source_prompt": row["source_prompt"],
                    "sample_idx": int(row.get("sample_idx", 0)),
                    "system_prompt": cfg["system_prompt"],
                    "response": response,
                    "cohort": cohort,
                    "generation_error": False,
                    "trapi": metadata,
                    "_group": group,
                }
            except Exception as exc:
                error = error_text(exc)
                return {
                    "condition_key": key,
                    "family": condition["family"],
                    "effort": condition["effort"],
                    "target_deployment": condition["deployment"],
                    "generation_id": generation_id(key, row),
                    "prompt_id": row["prompt_id"],
                    "category": row["category"],
                    "language": row["language"],
                    "prompt": row["prompt"],
                    "source_prompt": row["source_prompt"],
                    "sample_idx": int(row.get("sample_idx", 0)),
                    "system_prompt": cfg["system_prompt"],
                    "cohort": cohort,
                    "generation_error": True,
                    "error": error,
                    "policy_blocked": is_policy_block(error),
                    "_retryable": is_retryable(error),
                    "_group": group,
                }
        finally:
            controller.release()

    fatal: list[str] = []
    with ThreadPoolExecutor(max_workers=int(cfg["generation_workers"])) as pool:
        futures = [pool.submit(worker, condition, row) for condition, row in tasks]
        for future in as_completed(futures):
            result = future.result()
            key = result["condition_key"]
            group = result.pop("_group")
            retryable = bool(result.pop("_retryable", False))
            policy_blocked = bool(result.get("policy_blocked"))
            controllers[group].record(not result["generation_error"], retryable)
            with locks[key]:
                if policy_blocked:
                    audit = {**result, "refusal": True}
                    append_jsonl(condition_dir(cfg, key) / "policy_blocked_refusals.jsonl", audit)
                elif result["generation_error"]:
                    append_jsonl(condition_dir(cfg, key) / "generation_errors.jsonl", result)
                    if not retryable:
                        fatal.append(f"{key}: {result['error']}")
                else:
                    append_jsonl(condition_dir(cfg, key) / "generations.jsonl", result)
            state = controllers[group].to_dict()
            if state["total_requests"] % 10 == 0 or result["generation_error"]:
                save_concurrency_state(cfg, group, state)
            print(json.dumps({"stage": f"generate-{cohort}", "id": result["generation_id"], "error": result["generation_error"], "policy": policy_blocked, "cwnd": round(state["cwnd"], 3)}, ensure_ascii=False), flush=True)
    for group, controller in controllers.items():
        save_concurrency_state(cfg, group, controller.to_dict())
    if fatal:
        raise RuntimeError("Non-retryable generation errors:\n" + "\n".join(fatal[:20]))
    return len(tasks)


def qwen_judge_active() -> bool:
    result = subprocess.run(["ps", "-eo", "pid,args"], check=True, capture_output=True, text=True)
    for line in result.stdout.splitlines():
        lowered = line.lower()
        if "qwen3.5-27b" in lowered and "orbench_multimodel.py" in lowered and "judge-" in lowered:
            return True
    return False


def wait_for_qwen_judge(cfg: dict[str, Any]) -> None:
    while qwen_judge_active():
        print(json.dumps({"stage": "judge-paused", "reason": "qwen-judge-active"}), flush=True)
        time.sleep(int(cfg["qwen_judge_poll_seconds"]))


def generation_rows_for_manifest(cfg: dict[str, Any], condition_key: str, manifest_keys: set[tuple[str, str, int]]) -> list[dict[str, Any]]:
    return [
        row
        for row in read_jsonl(condition_dir(cfg, condition_key) / "generations.jsonl")
        if row_key(row) in manifest_keys and not row.get("generation_error") and row.get("response")
    ]


def run_judge_round(cfg: dict[str, Any], manifest: list[dict[str, Any]], cohort: str) -> int:
    manifest_keys = {row_key(row) for row in manifest}
    tasks = []
    for condition in cfg["conditions"]:
        key = condition["key"]
        completed = completed_judgment_ids(cfg, key)
        tasks.extend(row for row in generation_rows_for_manifest(cfg, key, manifest_keys) if row["generation_id"] not in completed)

    def worker(generation: dict[str, Any]) -> dict[str, Any]:
        raw = ""
        metadata: dict[str, Any] = {}
        try:
            raw, metadata = call_chat(
                cfg,
                cfg["response_judge_deployment"],
                "",
                orbench_response_judge_prompt(generation["prompt"], generation["response"]),
                cfg["response_judge_reasoning_effort"],
            )
            classification, rationale = parse_orbench_classification(raw)
            return {
                "condition_key": generation["condition_key"],
                "generation_id": generation["generation_id"],
                "prompt_id": generation["prompt_id"],
                "language": generation["language"],
                "category": generation["category"],
                "classification": classification,
                "rationale": rationale,
                "refusal": classification != "direct_answer",
                "judge_error": False,
                "judge_deployment": cfg["response_judge_deployment"],
                "judge_reasoning_effort": cfg["response_judge_reasoning_effort"],
                "raw": raw,
                "trapi": metadata,
                "cohort": generation["cohort"],
            }
        except Exception as exc:
            return {
                "condition_key": generation["condition_key"],
                "generation_id": generation["generation_id"],
                "prompt_id": generation["prompt_id"],
                "language": generation["language"],
                "category": generation["category"],
                "judge_error": True,
                "error": error_text(exc),
                "raw": raw,
                "trapi": metadata,
                "cohort": generation["cohort"],
            }

    submitted = 0
    batch_size = int(cfg["judge_workers"])
    for start in range(0, len(tasks), batch_size):
        wait_for_qwen_judge(cfg)
        batch = tasks[start : start + batch_size]
        with ThreadPoolExecutor(max_workers=batch_size) as pool:
            futures = [pool.submit(worker, generation) for generation in batch]
            for future in as_completed(futures):
                result = future.result()
                key = result["condition_key"]
                path = "response_judge_errors.jsonl" if result["judge_error"] else "response_judgments.jsonl"
                append_jsonl(condition_dir(cfg, key) / path, result)
                submitted += 1
                print(json.dumps({"stage": f"judge-{cohort}", "id": result["generation_id"], "error": result["judge_error"]}, ensure_ascii=False), flush=True)
    return submitted


def stage_counts(cfg: dict[str, Any], manifest: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    keys = {row_key(row) for row in manifest}
    generation_counts = {condition["key"]: len(completed_generation_keys(cfg, condition["key"]) & keys) for condition in cfg["conditions"]}
    expected_ids = {condition["key"]: {generation_id(condition["key"], row) for row in manifest} for condition in cfg["conditions"]}
    judgment_counts = {condition["key"]: len(completed_judgment_ids(cfg, condition["key"]) & expected_ids[condition["key"]]) for condition in cfg["conditions"]}
    return generation_counts, judgment_counts


def complete_stage(cfg: dict[str, Any], manifest: list[dict[str, Any]], cohort: str) -> None:
    expected = len(manifest)
    for round_no in range(1, int(cfg["max_stage_rounds"]) + 1):
        generation_counts, _ = stage_counts(cfg, manifest)
        print(json.dumps({"stage": f"generate-{cohort}", "round": round_no, "counts": generation_counts, "expected": expected}), flush=True)
        if all(count == expected for count in generation_counts.values()):
            break
        submitted = run_generation_round(cfg, manifest, cohort)
        if submitted == 0:
            raise RuntimeError(f"No generation progress in {cohort} round {round_no}")
    else:
        raise RuntimeError(f"Generation {cohort} did not complete")

    for round_no in range(1, int(cfg["max_stage_rounds"]) + 1):
        _, judgment_counts = stage_counts(cfg, manifest)
        print(json.dumps({"stage": f"judge-{cohort}", "round": round_no, "counts": judgment_counts, "expected": expected}), flush=True)
        if all(count == expected for count in judgment_counts.values()):
            return
        submitted = run_judge_round(cfg, manifest, cohort)
        if submitted == 0:
            raise RuntimeError(f"No judge progress in {cohort} round {round_no}")
    raise RuntimeError(f"Judge {cohort} did not complete")


def reasoning_tokens(row: dict[str, Any]) -> int | None:
    usage = (row.get("trapi") or {}).get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    value = details.get("reasoning_tokens")
    return int(value) if value is not None else None


def smoke_gate(cfg: dict[str, Any]) -> dict[str, Any]:
    smoke = read_jsonl(resolve(cfg["smoke_manifest"]))
    smoke_keys = {row_key(row) for row in smoke}
    generations, judgments = stage_counts(cfg, smoke)
    failures = []
    audits = {}
    zero_expected = {"gpt-5.6-sol-none", "gpt-5.5-none", "gpt-5-none", "grok-4.1-fast-non-reasoning"}
    positive_expected = {"gpt-5.6-sol-high", "gpt-5.5-high", "gpt-5-medium", "gpt-5-high", "grok-4.1-fast-reasoning"}
    for condition in cfg["conditions"]:
        key = condition["key"]
        rows = generation_rows_for_manifest(cfg, key, smoke_keys)
        tokens = [reasoning_tokens(row) for row in rows]
        observed = [value for value in tokens if value is not None]
        audits[key] = {"generated": generations[key], "judged": judgments[key], "usage_rows": len(observed), "reasoning_nonzero": sum(value > 0 for value in observed)}
        if generations[key] != 90 or judgments[key] != 90:
            failures.append(f"{key}: generation={generations[key]} judgment={judgments[key]}")
        if key in zero_expected and any(value != 0 for value in observed):
            failures.append(f"{key}: OFF condition produced nonzero reasoning tokens")
        if key in zero_expected and len(observed) != len(rows):
            failures.append(f"{key}: OFF condition missing usage reasoning token fields")
        if key in positive_expected and (not observed or not any(value > 0 for value in observed)):
            failures.append(f"{key}: ON condition did not demonstrate reasoning tokens")
    result = {"status": "failed" if failures else "passed", "conditions": audits, "failures": failures}
    path = resolve_output(cfg["output_dir"]) / "smoke_gate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"smoke_gate": result}, ensure_ascii=False), flush=True)
    if failures:
        raise RuntimeError("Smoke gate failed: " + "; ".join(failures))
    return result


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return math.nan, math.nan
    p = k / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def condition_outcomes(directory: Path) -> tuple[dict[tuple[str, str, int], dict[str, Any]], list[dict[str, Any]]]:
    generations = [row for row in read_jsonl(directory / "generations.jsonl") if not row.get("generation_error") and row.get("response")]
    judgments = {row["generation_id"]: row for row in read_jsonl(directory / "response_judgments.jsonl") if row.get("generation_id") and not row.get("judge_error")}
    outcomes = {}
    for generation in generations:
        judgment = judgments.get(generation["generation_id"])
        if judgment:
            outcomes[row_key(generation)] = {**generation, "refusal": bool(judgment.get("refusal"))}
    for row in read_jsonl(directory / "policy_blocked_refusals.jsonl"):
        if row.get("policy_blocked") and row.get("refusal"):
            outcomes[row_key(row)] = {**row, "refusal": True}
    return outcomes, generations


def all_condition_data(cfg: dict[str, Any]) -> tuple[dict[str, dict[tuple[str, str, int], dict[str, Any]]], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    outcomes: dict[str, dict[tuple[str, str, int], dict[str, Any]]] = {}
    generations: dict[str, list[dict[str, Any]]] = {}
    metadata = {condition["key"]: condition for condition in cfg["conditions"]}
    for condition in cfg["conditions"]:
        outcomes[condition["key"]], generations[condition["key"]] = condition_outcomes(condition_dir(cfg, condition["key"]))
    for baseline in cfg["baselines"]:
        outcomes[baseline["key"]], generations[baseline["key"]] = condition_outcomes(resolve_output(baseline["directory"]))
        metadata[baseline["key"]] = baseline
    return outcomes, generations, metadata


def percentile(values: list[int], fraction: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return float(ordered[index])


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        if fields:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)


def summarize(cfg: dict[str, Any]) -> None:
    outcomes, generations, metadata = all_condition_data(cfg)
    expected = 6336
    incomplete = {key: len(rows) for key, rows in outcomes.items() if len(rows) != expected}
    if incomplete:
        raise RuntimeError(f"Cannot summarize incomplete conditions: {incomplete}")
    language_to_resource = {language: group for group, languages in cfg["resource_groups"].items() for language in languages}
    rate_rows = []
    for condition_key, condition_outcome in sorted(outcomes.items()):
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in condition_outcome.values():
            groups[("overall", "all")].append(row)
            groups[("language", row["language"])].append(row)
            groups[("resource", language_to_resource[row["language"]])].append(row)
            groups[("topic", row["category"])].append(row)
            groups[("topic_language", f"{row['category']}|{row['language']}")].append(row)
        for (dimension, value), rows in sorted(groups.items()):
            refusals = sum(bool(row["refusal"]) for row in rows)
            low, high = wilson(refusals, len(rows))
            rate_rows.append({
                "condition_key": condition_key,
                "family": metadata[condition_key]["family"],
                "effort": metadata[condition_key]["effort"],
                "dimension": dimension,
                "value": value,
                "n_valid": len(rows),
                "n_refusal": refusals,
                "refusal_rate": refusals / len(rows),
                "ci_low": low,
                "ci_high": high,
            })

    comparisons = [
        ("gpt-5.6-sol-none", "gpt-5.6-sol-medium"), ("gpt-5.6-sol-medium", "gpt-5.6-sol-high"), ("gpt-5.6-sol-none", "gpt-5.6-sol-high"),
        ("gpt-5.5-none", "gpt-5.5-medium"), ("gpt-5.5-medium", "gpt-5.5-high"), ("gpt-5.5-none", "gpt-5.5-high"),
        ("gpt-5-none", "gpt-5-medium"), ("gpt-5-medium", "gpt-5-high"), ("gpt-5-none", "gpt-5-high"),
        ("grok-4.1-fast-non-reasoning", "grok-4.1-fast-reasoning"),
    ]
    delta_rows = []
    for left, right in comparisons:
        shared = set(outcomes[left]) & set(outcomes[right])
        grouped: dict[tuple[str, str], list[tuple[bool, bool]]] = defaultdict(list)
        for key in shared:
            a, b = outcomes[left][key], outcomes[right][key]
            pairs = [("overall", "all"), ("language", a["language"]), ("resource", language_to_resource[a["language"]]), ("topic", a["category"]), ("topic_language", f"{a['category']}|{a['language']}")]
            for group in pairs:
                grouped[group].append((bool(a["refusal"]), bool(b["refusal"])))
        for (dimension, value), pairs in sorted(grouped.items()):
            delta_rows.append({
                "from_condition": left,
                "to_condition": right,
                "dimension": dimension,
                "value": value,
                "n_paired": len(pairs),
                "from_refusal_rate": sum(a for a, _ in pairs) / len(pairs),
                "to_refusal_rate": sum(b for _, b in pairs) / len(pairs),
                "delta_percentage_points": 100 * (sum(b for _, b in pairs) - sum(a for a, _ in pairs)) / len(pairs),
            })

    audit_rows = []
    for condition_key, rows in sorted(generations.items()):
        tokens = [value for value in (reasoning_tokens(row) for row in rows) if value is not None]
        audit_rows.append({
            "condition_key": condition_key,
            "n_generation": len(rows),
            "n_usage": len(tokens),
            "n_reasoning_nonzero": sum(value > 0 for value in tokens),
            "reasoning_nonzero_rate": sum(value > 0 for value in tokens) / len(tokens) if tokens else math.nan,
            "reasoning_tokens_mean": statistics.mean(tokens) if tokens else math.nan,
            "reasoning_tokens_median": statistics.median(tokens) if tokens else math.nan,
            "reasoning_tokens_p95": percentile(tokens, 0.95),
        })

    summary_dir = resolve_output(cfg["output_dir"]) / "summary"
    write_csv(summary_dir / "refusal_rates.csv", rate_rows)
    write_csv(summary_dir / "effort_deltas.csv", delta_rows)
    write_csv(summary_dir / "reasoning_audit.csv", audit_rows)
    write_markdown_report(cfg, rate_rows, delta_rows, summary_dir / "reasoning-results-v2.md")
    write_artifact_manifest(resolve_output(cfg["output_dir"]))


def percent(value: float) -> str:
    return f"{100 * value:.2f}%"


def write_markdown_report(cfg: dict[str, Any], rates: list[dict[str, Any]], deltas: list[dict[str, Any]], path: Path) -> None:
    keys = [condition["key"] for condition in cfg["conditions"]] + [baseline["key"] for baseline in cfg["baselines"]]
    rate_index = {(row["condition_key"], row["dimension"], row["value"]): row for row in rates}
    lines = ["# OR-Bench Reasoning Effort 实验结果", "", "## Overall refusal rate", "", "| Condition | Refusal rate | Valid n |", "|---|---:|---:|"]
    for key in keys:
        row = rate_index[(key, "overall", "all")]
        lines.append(f"| {key} | {percent(row['refusal_rate'])} | {row['n_valid']} |")
    for title, dimension, values in [
        ("Language refusal rate", "language", cfg["languages"]),
        ("Resource-group refusal rate", "resource", ["high", "medium", "low"]),
    ]:
        lines.extend(["", f"## {title}", "", "| Condition | " + " | ".join(values) + " |", "|---|" + "---:|" * len(values)])
        for key in keys:
            cells = [percent(rate_index[(key, dimension, value)]["refusal_rate"]) for value in values]
            lines.append("| " + key + " | " + " | ".join(cells) + " |")
    topics = sorted({row["value"].split("|", 1)[0] for row in rates if row["dimension"] == "topic_language"})
    for topic in topics:
        lines.extend(["", f"## Topic: {topic}", "", "| Condition | " + " | ".join(cfg["languages"]) + " |", "|---|" + "---:|" * len(cfg["languages"])])
        for key in keys:
            cells = [percent(rate_index[(key, "topic_language", f"{topic}|{language}")]["refusal_rate"]) for language in cfg["languages"]]
            lines.append("| " + key + " | " + " | ".join(cells) + " |")
    lines.extend(["", "## Overall effort deltas", "", "| From | To | Delta (percentage points) | Paired n |", "|---|---|---:|---:|"])
    for row in deltas:
        if row["dimension"] == "overall":
            lines.append(f"| {row['from_condition']} | {row['to_condition']} | {row['delta_percentage_points']:+.2f} | {row['n_paired']} |")
    lines.extend(["", "GPT-5.5 conditions use GPT-5.5 medium-effort self-judge.", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_artifact_manifest(output_dir: Path) -> None:
    rows = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name == "artifact_manifest.json":
            continue
        with path.open("rb") as handle:
            line_count = sum(1 for _ in handle)
        rows.append({"path": str(path.relative_to(ROOT)), "size_bytes": path.stat().st_size, "line_count": line_count, "sha256": sha256(path)})
    (output_dir / "artifact_manifest.json").write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(cfg: dict[str, Any]) -> None:
    preflight(cfg)
    smoke = read_jsonl(resolve(cfg["smoke_manifest"]))
    full = read_jsonl(resolve(cfg["full_manifest"]))
    complete_stage(cfg, smoke, "smoke")
    smoke_gate(cfg)
    complete_stage(cfg, full, "full")
    summarize(cfg)
    print(json.dumps({"status": "complete", "conditions": len(cfg["conditions"]), "expected_per_condition": 6336}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["preflight", "run", "status", "summarize"])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    cfg = load_json(args.config.resolve())
    if args.command == "preflight":
        preflight(cfg)
    elif args.command == "run":
        run(cfg)
    elif args.command == "summarize":
        summarize(cfg)
    else:
        manifest = read_jsonl(resolve(cfg["full_manifest"]))
        generation_counts, judgment_counts = stage_counts(cfg, manifest)
        print(json.dumps({"generation": generation_counts, "judgment": judgment_counts, "expected": len(manifest), "qwen_judge_active": qwen_judge_active()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
