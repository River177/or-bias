"""Unified OR-Bias command line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

from orbias.datasets import audit_duplicates, prepare, releases, unify_external, unify_orbench
from orbias.evaluation import multimodel, report
from orbias.paths import artifact_root
from orbias.reasoning import runner as reasoning_runner
from orbias.translation import finalize, runner as translation_runner


def _invoke(entry: Callable[[], None], argv: list[str]) -> None:
    previous = sys.argv
    try:
        sys.argv = [previous[0], *argv]
        entry()
    finally:
        sys.argv = previous


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orbias", description=__doc__)
    parser.add_argument("--artifact-root", type=Path, help="Overrides ORBIAS_ARTIFACT_ROOT")
    groups = parser.add_subparsers(dest="group", required=True)

    data = groups.add_parser("data", help="Prepare, audit, fetch, and verify datasets")
    data_actions = data.add_subparsers(dest="action", required=True)
    for action in ("prepare", "audit"):
        data_actions.add_parser(action)
    unify = data_actions.add_parser("unify")
    unify.add_argument("kind", choices=("external", "orbench"), nargs="?", default="external")
    for action in ("fetch", "verify"):
        command = data_actions.add_parser(action)
        command.add_argument("release", choices=("external-overrefusal-v1", "orbench-v2-audit", "all"))

    translate = groups.add_parser("translate", help="Translate and quality-audit datasets")
    translate_actions = translate.add_subparsers(dest="action", required=True)
    for action in ("dry-run", "preflight", "status", "finalize"):
        translate_actions.add_parser(action)
    for action in ("smoke", "run"):
        command = translate_actions.add_parser(action)
        command.add_argument("--dataset", action="append", default=[])

    evaluate = groups.add_parser("evaluate", help="Generate, judge, summarize, and report")
    evaluate_actions = evaluate.add_subparsers(dest="action", required=True)
    evaluate_actions.add_parser("prepare")
    for action in ("generate", "judge", "summarize"):
        command = evaluate_actions.add_parser(action)
        command.add_argument("--mode", choices=("smoke", "full"), default="full")
    evaluate_actions.add_parser("report")

    reasoning = groups.add_parser("reasoning", help="Run reasoning-effort experiments")
    reasoning_actions = reasoning.add_subparsers(dest="action", required=True)
    for action in ("preflight", "run", "status", "summarize"):
        reasoning_actions.add_parser(action)
    return parser


def main() -> None:
    parser = build_parser()
    args, remaining = parser.parse_known_args()
    resolved_artifact_root = artifact_root(args.artifact_root)
    if args.artifact_root is not None:
        os.environ["ORBIAS_ARTIFACT_ROOT"] = str(resolved_artifact_root)

    if args.group == "data":
        if args.action == "prepare":
            _invoke(prepare.main, remaining)
        elif args.action == "audit":
            _invoke(audit_duplicates.main, remaining)
        elif args.action == "unify":
            _invoke(unify_orbench.main if args.kind == "orbench" else unify_external.main, remaining)
        else:
            names = ["external-overrefusal-v1", "orbench-v2-audit"] if args.release == "all" else [args.release]
            output = []
            for name in names:
                values = releases.fetch(name, output_root=resolved_artifact_root) if args.action == "fetch" else releases.verify(name, output_root=resolved_artifact_root)
                output.append({"release": name, "artifacts": [str(value) for value in values]})
            print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    if args.group == "translate":
        if args.action == "finalize":
            _invoke(finalize.main, remaining)
        else:
            command = {"smoke": "run-smoke", "run": "run-full"}.get(args.action, args.action)
            datasets = [item for value in getattr(args, "dataset", []) for item in ("--dataset", value)]
            _invoke(translation_runner.main, [command, *datasets, *remaining])
        return

    if args.group == "evaluate":
        if args.action == "report":
            _invoke(report.main, remaining)
        else:
            command = "prepare" if args.action == "prepare" else f"{args.action}-{args.mode}"
            _invoke(multimodel.main, [command, *remaining])
        return

    _invoke(reasoning_runner.main, [args.action, *remaining])


if __name__ == "__main__":
    main()

