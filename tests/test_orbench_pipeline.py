import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import orbench_pipeline as pipeline  # noqa: E402
import orbench_multimodel as multimodel  # noqa: E402


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

    def test_multimodel_config_has_three_canonical_models(self):
        cfg = multimodel.load_simple_yaml(Path(__file__).parents[1] / "configs" / "orbench_multilingual_v2.yaml")
        self.assertEqual({model["key"] for model in cfg["models"]}, {"qwen3.5-27b", "gpt-4o", "gemma-3-27b-it"})
        self.assertEqual(cfg["languages"], ["en", "zh", "ja", "ko", "sv", "da", "ta", "mn", "sw"])


if __name__ == "__main__":
    unittest.main()
