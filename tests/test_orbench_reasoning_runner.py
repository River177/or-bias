import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("orbench_reasoning_runner", ROOT / "scripts" / "orbench_reasoning_runner.py")
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


class ReasoningRunnerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = runner.load_json(ROOT / "configs" / "experiments" / "reasoning-v2.json")

    def test_config_has_nine_isolated_conditions_and_two_baselines(self):
        keys = {condition["key"] for condition in self.cfg["conditions"]}
        self.assertEqual(len(keys), 9)
        self.assertEqual({baseline["key"] for baseline in self.cfg["baselines"]}, {"gpt-5.6-sol-medium", "gpt-5.5-medium"})
        self.assertEqual(
            {condition["deployment"] for condition in self.cfg["conditions"] if condition["family"] == "grok-4.1-fast"},
            {"grok-4-1-fast-non-reasoning_1", "grok-4-1-fast-reasoning_1"},
        )

    def test_gpt_efforts_are_explicit_and_grok_uses_deployment_pair(self):
        for condition in self.cfg["conditions"]:
            if condition["family"].startswith("gpt-"):
                self.assertIn(condition["effort"], {"none", "medium", "high"})
            else:
                self.assertIn(condition["effort"], {"reasoning", "non-reasoning"})

    def test_no_client_token_limit_is_set(self):
        source = (ROOT / "scripts" / "orbench_reasoning_runner.py").read_text(encoding="utf-8")
        forbidden = "max" + "_tokens"
        forbidden_completion = "max" + "_completion_tokens"
        self.assertNotIn(forbidden, source)
        self.assertNotIn(forbidden_completion, source)

    def test_call_chat_only_passes_reasoning_effort_when_requested(self):
        response = mock.Mock()
        response.choices = [mock.Mock(message=mock.Mock(content="ok"))]
        response.model_dump.return_value = {"usage": {}}
        client = mock.Mock()
        client.chat.completions.create.return_value = response
        with mock.patch.object(runner, "get_client", return_value=client):
            runner.call_chat(self.cfg, "gpt", "system", "user", "high")
            self.assertEqual(client.chat.completions.create.call_args.kwargs["reasoning_effort"], "high")
            runner.call_chat(self.cfg, "grok", "system", "user", None)
            self.assertNotIn("reasoning_effort", client.chat.completions.create.call_args.kwargs)

    def test_completed_keys_include_policy_refusal_without_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {**self.cfg, "output_dir": tmp}
            directory = runner.condition_dir(cfg, "gpt-5-none")
            directory.mkdir(parents=True)
            policy = {
                "generation_id": "gpt-5-none:p1:en:0",
                "prompt_id": "p1",
                "language": "en",
                "sample_idx": 0,
                "policy_blocked": True,
                "refusal": True,
            }
            (directory / "policy_blocked_refusals.jsonl").write_text(json.dumps(policy) + "\n", encoding="utf-8")
            self.assertEqual(runner.completed_generation_keys(cfg, "gpt-5-none"), {("p1", "en", 0)})
            self.assertFalse((directory / "generations.jsonl").exists())

    def test_smoke_rows_are_reused_by_full_unique_key(self):
        row = {"prompt_id": "p1", "language": "en", "sample_idx": 0}
        self.assertEqual(runner.generation_id("gpt-5-none", row), "gpt-5-none:p1:en:0")
        self.assertEqual(runner.row_key(row), ("p1", "en", 0))


if __name__ == "__main__":
    unittest.main()
