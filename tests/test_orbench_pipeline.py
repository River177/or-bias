import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import orbench_pipeline as pipeline  # noqa: E402
import orbench_multimodel as multimodel  # noqa: E402
import orbench_stage_runner as stage_runner  # noqa: E402
import orbench_panel_report as panel_report  # noqa: E402


class PipelineUnitTests(unittest.TestCase):
    def test_config_parser_preserves_languages_and_exact_system_prompt(self):
        cfg = pipeline.load_simple_yaml(Path(__file__).parents[1] / "configs" / "orbench_multilingual_v2.yaml")
        self.assertEqual(cfg["languages"], ["en", "zh", "ja", "ko", "sv", "da", "ta", "mn", "sw"])
        self.assertEqual(cfg["high_resource_languages"], ["en", "zh", "ja"])
        self.assertEqual(cfg["medium_resource_languages"], ["ko", "sv", "da"])
        self.assertEqual(cfg["low_resource_languages"], ["ta", "mn", "sw"])
        self.assertEqual(set(cfg["languages"]), set(cfg["high_resource_languages"] + cfg["medium_resource_languages"] + cfg["low_resource_languages"]))
        self.assertNotIn("es", cfg["languages"])
        self.assertNotIn("ar", cfg["languages"])
        self.assertEqual(cfg["system_prompt"], "You are a helpful assistant.")
        self.assertEqual(cfg["translator_deployment"], "gpt-5.6-sol_2026-07-09")
        self.assertEqual(cfg["response_judge_deployment"], "gpt-5.5_2026-04-24")

    def test_stable_id_is_deterministic(self):
        self.assertEqual(pipeline.stable_id("hello", "privacy"), pipeline.stable_id("hello", "privacy"))
        self.assertNotEqual(pipeline.stable_id("hello", "privacy"), pipeline.stable_id("hello", "hate"))

    def test_extract_json_accepts_fenced_or_plain_json(self):
        expected = {"translation": "你好"}
        self.assertEqual(pipeline.extract_json(json.dumps(expected)), expected)
        self.assertEqual(pipeline.extract_json("Here is the result: " + json.dumps(expected)), expected)

    def test_wilson_interval_contains_empirical_rate(self):
        low, high = pipeline.wilson(5, 10)
        self.assertLessEqual(low, 0.5)
        self.assertGreaterEqual(high, 0.5)

    def test_prepare_uses_current_full_snapshot(self):
        cfg = pipeline.load_simple_yaml(Path(__file__).parents[1] / "configs" / "orbench_multilingual_v2.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            old_exp = pipeline.EXP
            old_frozen = pipeline.FROZEN_DIR
            try:
                pipeline.EXP = Path(tmp)
                pipeline.FROZEN_DIR = Path(tmp)
                pipeline.cmd_prepare(cfg)
                rows = pipeline.read_jsonl(Path(tmp) / "manifest.jsonl")
                self.assertEqual(len(rows), 1319)
                self.assertEqual(len({r["prompt_id"] for r in rows}), 1319)
            finally:
                pipeline.EXP = old_exp
                pipeline.FROZEN_DIR = old_frozen

    def test_multimodel_config_has_eleven_canonical_models(self):
        cfg = multimodel.load_simple_yaml(Path(__file__).parents[1] / "configs" / "orbench_multilingual_v2.yaml")
        expected = {
            "grok-4.3": "grok-4.3_1",
            "deepseek-v4-flash": "DeepSeek-V4-Flash_2026-04-23",
            "kimi-k2.6": "Kimi-K2.6_2026-04-20",
            "gpt-4o": "gpt-4o_2024-11-20",
            "gpt-5.6-sol": "gpt-5.6-sol_2026-07-09",
            "gpt-5.5": "gpt-5.5_2026-04-24",
            "gpt-5": "gpt-5_2025-08-07",
            "gemma-4-31b-it": "google/gemma-4-31B-it",
            "qwen3.5-27b": "Qwen/Qwen3.5-27B",
            "kimi-k2.5": "Kimi-K2.5_1",
            "llama-3.3-70b-instruct": "Llama-3.3-70B-Instruct_5",
        }
        self.assertEqual({model["key"]: model["deployment"] for model in cfg["models"]}, expected)
        new_keys = set(expected) - {"grok-4.3", "deepseek-v4-flash", "kimi-k2.6", "gpt-4o"}
        self.assertEqual({model["workers"] for model in cfg["models"] if model["key"] in new_keys - {"qwen3.5-27b"}}, {16})
        qwen = next(model for model in cfg["models"] if model["key"] == "qwen3.5-27b")
        self.assertEqual((qwen["workers"], qwen["max_workers"]), (2, 16))
        self.assertEqual({model["max_workers"] for model in cfg["models"] if model["key"] in new_keys - {"qwen3.5-27b"}}, {32})
        self.assertEqual(cfg["generation_workers"], 208)
        self.assertEqual(cfg["judge_workers"], 64)
        self.assertEqual(cfg["languages"], ["en", "zh", "ja", "ko", "sv", "da", "ta", "mn", "sw"])

    def test_generation_tasks_interleave_models(self):
        cfg = {"models": [{"key": "a", "deployment": "a"}, {"key": "b", "deployment": "b"}]}
        rows = [{"prompt_id": "1"}, {"prompt_id": "2"}]
        tasks = multimodel.generation_tasks(rows, cfg, "smoke")
        self.assertEqual([(t["row"]["prompt_id"], t["model_key"]) for t in tasks], [("1", "a"), ("1", "b"), ("2", "a"), ("2", "b")])

    def test_adaptive_concurrency_uses_persistent_tcp_reno_aimd(self):
        gate = multimodel.AdaptiveConcurrency(4, max_concurrency=8, ssthresh=8)
        for _ in range(4):
            gate.record(True, False)
        self.assertEqual(gate.target, 8)
        self.assertEqual(gate.record(False, True), "multiplicative_decrease")
        self.assertEqual(gate.target, 4)
        self.assertEqual(gate.record(False, True), "congestion_during_recovery")
        self.assertEqual(gate.target, 4)
        state = gate.to_dict()
        resumed = multimodel.AdaptiveConcurrency(1, max_concurrency=8, state=state)
        self.assertEqual(resumed.target, 4)
        self.assertEqual(resumed.to_dict()["algorithm"], "tcp-reno-aimd-v1")

    def test_smoke_manifest_has_ten_rows_per_language_and_all_categories(self):
        cfg = multimodel.load_simple_yaml(Path(__file__).parents[1] / "configs" / "orbench_multilingual_v2.yaml")
        rows = multimodel.load_test_rows(cfg)
        smoke = multimodel.select_smoke_rows(rows, cfg)
        self.assertEqual(len(smoke), 90)
        for language in cfg["languages"]:
            language_rows = [row for row in smoke if row["language"] == language]
            self.assertEqual(len(language_rows), 10)
            self.assertEqual(len({row["category"] for row in language_rows}), 10)

    def test_llm_callers_do_not_set_client_token_limits(self):
        pipeline_source = Path(pipeline.__file__).read_text(encoding="utf-8")
        multimodel_source = Path(multimodel.__file__).read_text(encoding="utf-8")
        for source in (pipeline_source, multimodel_source):
            self.assertNotIn("max_completion_tokens", source)
            self.assertNotIn('kwargs["max_tokens"]', source)

    def test_stage_runner_counts_only_unique_successes(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "models" / "test-model"
            model_dir.mkdir(parents=True)
            generation = {"prompt_id": "p1", "language": "en", "sample_idx": 0, "generation_id": "test-model:p1:en:0"}
            (model_dir / "generations.jsonl").write_text(
                "\n".join(json.dumps(row) for row in [generation, generation, {**generation, "generation_error": True}]) + "\n",
                encoding="utf-8",
            )
            judgment = {"generation_id": generation["generation_id"], "judge_error": False}
            (model_dir / "response_judgments.jsonl").write_text(
                "\n".join(json.dumps(row) for row in [judgment, judgment, {**judgment, "judge_error": True}]) + "\n",
                encoding="utf-8",
            )
            policy = {"generation_id": "test-model:p2:zh:0", "prompt_id": "p2", "language": "zh", "sample_idx": 0, "policy_blocked": True, "refusal": True}
            (model_dir / "policy_blocked_refusals.jsonl").write_text(json.dumps(policy) + "\n", encoding="utf-8")
            self.assertEqual(stage_runner.generation_count(Path(tmp), "test-model"), 2)
            self.assertEqual(stage_runner.judgment_count(Path(tmp), "test-model"), 2)

    def test_panel_report_formats_only_refusal_rate_and_denominator(self):
        self.assertEqual(panel_report.rate_cell(1, 4), "25.00% (n=4)")
        self.assertEqual(panel_report.rate_cell(0, 0), "NA")


if __name__ == "__main__":
    unittest.main()
