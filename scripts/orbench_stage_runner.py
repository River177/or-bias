#!/usr/bin/env python3
"""Complete selected OR-Bench model stages with resumable retry rounds."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import orbench_multimodel as multimodel

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "orbench_multimodel.py"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def generation_count(experiment_dir: Path, model_key: str) -> int:
    rows = read_jsonl(experiment_dir / "models" / model_key / "generations.jsonl")
    policy = read_jsonl(experiment_dir / "models" / model_key / "policy_blocked_refusals.jsonl")
    keys = {(row.get("prompt_id"), row.get("language"), int(row.get("sample_idx", 0))) for row in rows if not row.get("generation_error")}
    keys.update((row.get("prompt_id"), row.get("language"), int(row.get("sample_idx", 0))) for row in policy if row.get("policy_blocked") and row.get("refusal"))
    return len(keys)


def judgment_count(experiment_dir: Path, model_key: str) -> int:
    rows = read_jsonl(experiment_dir / "models" / model_key / "response_judgments.jsonl")
    policy = read_jsonl(experiment_dir / "models" / model_key / "policy_blocked_refusals.jsonl")
    keys = {row.get("generation_id") for row in rows if row.get("generation_id") and not row.get("judge_error")}
    keys.update(row.get("generation_id") for row in policy if row.get("generation_id") and row.get("policy_blocked") and row.get("refusal"))
    return len(keys)


def wait_for_pid(pid: int | None) -> None:
    if pid is None:
        return
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        print(json.dumps({"waiting_for_pid": pid}), flush=True)
        time.sleep(30)


def run_command(config: Path, command: str, models: list[str], workers: int | None, max_workers: int | None, timeout: int | None) -> None:
    argv = [sys.executable, str(RUNNER), command, "--config", str(config), "--models", ",".join(models)]
    if command.startswith("generate") and workers is not None:
        argv += ["--workers", str(workers)]
    if command.startswith("generate") and max_workers is not None:
        argv += ["--max-workers", str(max_workers)]
    if timeout is not None:
        argv += ["--call-timeout-seconds", str(timeout)]
    subprocess.run(argv, cwd=ROOT, check=True)


def complete_stage(
    config: Path,
    experiment_dir: Path,
    models: list[str],
    pass_name: str,
    expected: int,
    workers: int | None,
    max_workers: int | None,
    timeout: int | None,
    max_rounds: int,
) -> None:
    for round_no in range(1, max_rounds + 1):
        generation_counts = {model: generation_count(experiment_dir, model) for model in models}
        print(json.dumps({"stage": f"generate-{pass_name}", "round": round_no, "counts": generation_counts, "expected": expected}), flush=True)
        if all(count == expected for count in generation_counts.values()):
            break
        run_command(config, f"generate-{pass_name}", models, workers, max_workers, timeout)
    else:
        raise RuntimeError(f"Generation did not reach {expected} after {max_rounds} rounds")

    for round_no in range(1, max_rounds + 1):
        judgment_counts = {model: judgment_count(experiment_dir, model) for model in models}
        print(json.dumps({"stage": f"judge-{pass_name}", "round": round_no, "counts": judgment_counts, "expected": expected}), flush=True)
        if all(count == expected for count in judgment_counts.values()):
            break
        run_command(config, f"judge-{pass_name}", models, None, None, timeout)
    else:
        raise RuntimeError(f"Judgments did not reach {expected} after {max_rounds} rounds")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["smoke-full", "full"])
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "orbench_multilingual_v2.yaml")
    parser.add_argument("--models", required=True, help="Comma-separated configured model keys")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--max-workers", type=int, default=None)
    parser.add_argument("--call-timeout-seconds", type=int, default=None)
    parser.add_argument("--wait-pid", type=int, default=None)
    parser.add_argument("--max-rounds", type=int, default=100)
    args = parser.parse_args()

    config = args.config.resolve()
    cfg = multimodel.load_simple_yaml(config)
    configured = {model["key"] for model in cfg["models"]}
    models = [key.strip() for key in args.models.split(",") if key.strip()]
    unknown = set(models) - configured
    if unknown:
        raise SystemExit(f"Unknown configured model keys: {','.join(sorted(unknown))}")
    if args.workers is not None and args.workers < 1:
        raise SystemExit("--workers must be at least 1")
    if args.max_rounds < 1:
        raise SystemExit("--max-rounds must be at least 1")

    experiment_dir = Path(str(cfg.get("experiment_dir", "experiments/v2")))
    if not experiment_dir.is_absolute():
        experiment_dir = ROOT / experiment_dir

    wait_for_pid(args.wait_pid)
    if args.stage == "smoke-full":
        complete_stage(config, experiment_dir, models, "smoke", 90, args.workers, args.max_workers, args.call_timeout_seconds, args.max_rounds)
    complete_stage(config, experiment_dir, models, "full", 6336, args.workers, args.max_workers, args.call_timeout_seconds, args.max_rounds)
    run_command(config, "summarize-full", models, None, None, args.call_timeout_seconds)
    print(json.dumps({"status": "complete", "models": models}), flush=True)


if __name__ == "__main__":
    main()
