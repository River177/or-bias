import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "multilingual_translation_runner.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import multilingual_translation_runner as runner  # noqa: E402
import orbench_pipeline as legacy_pipeline  # noqa: E402


class MultilingualTranslationRunnerTests(unittest.TestCase):
    def test_dry_run_orders_datasets_by_size_and_skips_orbench(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            datasets = []
            for name, count in (("small", 2), ("large", 3)):
                path = root / f"{name}.jsonl"
                with path.open("w", encoding="utf-8") as handle:
                    for index in range(count):
                        handle.write(
                            json.dumps(
                                {
                                    "record_id": f"{name}-{index}",
                                    "dataset": name,
                                    "prompt": f"prompt {index}",
                                    "prompt_sha256": "unused-in-test",
                                    "category": None,
                                    "language": "en",
                                }
                            )
                            + "\n"
                        )
                datasets.append({"key": name, "path": str(path), "rows": count})
            config = {
                "languages": ["en", "zh", "ja"],
                "datasets": list(reversed(datasets)),
                "skip_datasets": ["orbench"],
                "smoke_per_dataset": 1,
                "artifact_root": str(root / "artifacts"),
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "dry-run", "--config", str(config_path)],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            result = json.loads(completed.stdout)
            self.assertEqual([item["dataset"] for item in result["datasets"]], ["small", "large"])
            self.assertEqual([item["full_translation_tasks"] for item in result["datasets"]], [4, 6])
            self.assertEqual(result["skipped_datasets"], ["orbench"])

    def test_controller_grows_on_success_and_halves_on_429(self) -> None:
        controller = runner.CongestionController(
            initial_cwnd=4,
            ssthresh=32,
            min_cwnd=1,
            max_cwnd=64,
            window_size=50,
        )
        for _ in range(4):
            controller.record_success(1.0)
        self.assertGreater(controller.cwnd, 4)
        before_429 = controller.cwnd
        controller.record_congestion("429", retry_after=10.0)
        self.assertEqual(controller.cwnd, max(1, before_429 / 2))
        self.assertGreater(controller.cooldown_until, 0)

    def test_translation_and_judge_prompts_are_exactly_reused(self) -> None:
        self.assertEqual(runner.TRANSLATION_SYSTEM, legacy_pipeline.TRANSLATION_SYSTEM)
        self.assertEqual(runner.TRANSLATION_JUDGE_SYSTEM, legacy_pipeline.TRANSLATION_JUDGE_SYSTEM)
        row = {"prompt": "Original prompt", "category": None}
        translation = {"language": "sv", "translated_prompt": "Översättning"}
        self.assertEqual(
            json.loads(runner.translation_prompt(row, "Swedish")),
            {
                "source_language": "English",
                "target_language": "Swedish",
                "category": None,
                "prompt": "Original prompt",
            },
        )
        self.assertEqual(
            json.loads(runner.translation_judge_prompt(row, translation)),
            {
                "original_prompt": "Original prompt",
                "original_category": None,
                "target_language": "sv",
                "translated_prompt": "Översättning",
            },
        )

    def test_task_builder_skips_successful_keys_on_resume(self) -> None:
        rows = [
            {"record_id": "a", "prompt": "A", "category": None},
            {"record_id": "b", "prompt": "B", "category": None},
        ]
        tasks = runner.build_translation_tasks(
            "sample",
            rows,
            ["zh", "ja"],
            completed_keys={("a", "zh")},
        )
        self.assertEqual(
            [(task["record_id"], task["language"]) for task in tasks],
            [("a", "ja"), ("b", "zh"), ("b", "ja")],
        )

    def test_judge_keep_is_recomputed_from_strict_fields(self) -> None:
        judgment = {
            "semantic_equivalence": "minor_degradation",
            "task_intent_preserved": True,
            "referents_preserved": True,
            "scope_preserved": True,
            "benign_intent_preserved": True,
            "category_preserved": True,
            "harmfulness_introduced": False,
            "naturalness": 5,
            "decision": "keep",
            "reason": "model said keep",
        }
        normalized = runner.normalize_judgment(judgment, naturalness_min=4)
        self.assertEqual(normalized["decision"], "repair")
        self.assertFalse(normalized["strict_keep"])

    def test_adaptive_runner_retries_without_writing_failure_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            controller = runner.CongestionController(
                initial_cwnd=2,
                ssthresh=4,
                min_cwnd=1,
                max_cwnd=4,
                window_size=10,
            )

            def worker(task: dict) -> dict:
                if task["record_id"] == "a" and task["attempt"] == 1:
                    raise ValueError("invalid JSON")
                return {"record_id": task["record_id"], "language": task["language"]}

            result = runner.run_adaptive(
                stage="translation",
                dataset="sample",
                tasks=[
                    {"record_id": "a", "language": "zh", "attempt": 1},
                    {"record_id": "b", "language": "zh", "attempt": 1},
                ],
                worker=worker,
                controller=controller,
                success_path=root / "success.jsonl",
                error_path=root / "errors.jsonl",
                attempt_error_path=root / "attempt_errors.jsonl",
                controller_events_path=root / "events.jsonl",
                state_path=root / "state.json",
                status_path=root / "status.json",
                max_attempts=2,
            )
            self.assertEqual(result["succeeded"], 2)
            self.assertEqual(result["exhausted"], 0)
            self.assertEqual(len(runner.read_jsonl(root / "success.jsonl")), 2)
            self.assertEqual(len(runner.read_jsonl(root / "errors.jsonl")), 0)
            self.assertEqual(len(runner.read_jsonl(root / "attempt_errors.jsonl")), 1)


if __name__ == "__main__":
    unittest.main()
