import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orbias.core import ArtifactStore, ErrorDisposition, FileLock, ResumableBatchRunner, TrapiClient
from orbias.paths import artifact_root


class ArtifactStoreTests(unittest.TestCase):
    def test_append_unique_atomic_state_manifest_and_verification(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = ArtifactStore(temporary_directory)
            self.assertEqual(
                store.append_unique("rows.jsonl", [{"id": "a"}, {"id": "a"}, {"id": "b"}], key=lambda row: row["id"]),
                2,
            )
            self.assertEqual(store.append_unique("rows.jsonl", [{"id": "b"}], key=lambda row: row["id"]), 0)
            self.assertEqual(store.keys("rows.jsonl", lambda row: row["id"]), {"a", "b"})
            state_path = store.write_json_atomic("state.json", {"complete": True})
            self.assertEqual(json.loads(state_path.read_text())["complete"], True)
            summary = store.summarize("rows.jsonl")
            self.assertEqual(summary.lines, 2)
            store.verify([summary])

    def test_lock_is_exclusive_and_released(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".lock"
            with FileLock(path):
                with self.assertRaises(RuntimeError):
                    FileLock(path).acquire()
            self.assertFalse(path.exists())


class ResumableBatchRunnerTests(unittest.TestCase):
    def test_retry_resume_and_nonretryable_error_isolation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            attempts = {"retry": 0}

            def worker(task):
                if task["id"] == "retry" and attempts["retry"] == 0:
                    attempts["retry"] += 1
                    raise TimeoutError("transient")
                if task["id"] == "bad":
                    raise ValueError("invalid_prompt")
                return dict(task)

            runner = ResumableBatchRunner(
                ArtifactStore(temporary_directory),
                success_path="success.jsonl",
                error_path="errors.jsonl",
                key=lambda row: row["id"],
                worker=worker,
                classify_error=lambda exc: ErrorDisposition.RETRYABLE if isinstance(exc, TimeoutError) else ErrorDisposition.NONRETRYABLE,
                concurrency=2,
                max_attempts=2,
                backoff_seconds=0,
            )
            result = runner.run([{"id": "ok"}, {"id": "retry"}, {"id": "bad"}])
            self.assertEqual((result.succeeded, result.failed, result.retried), (2, 1, 1))
            resumed = runner.run([{"id": "ok"}, {"id": "retry"}])
            self.assertEqual((resumed.submitted, resumed.skipped), (0, 2))


class TrapiClientTests(unittest.TestCase):
    def test_request_has_no_client_token_limit_and_rejects_override(self):
        response = mock.Mock()
        response.choices = [mock.Mock(message=mock.Mock(content="ok"))]
        response.model_dump.return_value = {"usage": {}}
        transport = mock.Mock()
        transport.chat.completions.create.return_value = response
        client = TrapiClient("redmond/interactive", "2024-10-21", timeout_seconds=30)
        with mock.patch.object(client, "_client", return_value=transport):
            client.chat(deployment="model", system="system", user="user")
            kwargs = transport.chat.completions.create.call_args.kwargs
            self.assertEqual(set(kwargs), {"model", "messages"})
            forbidden = {"max" + "_tokens": 1}
            with self.assertRaises(ValueError):
                client.chat(deployment="model", system="system", user="user", **forbidden)

    def test_all_runtime_callers_omit_client_token_limits(self):
        root = Path(__file__).resolve().parents[1] / "src" / "orbias"
        forbidden = ("max" + "_tokens", "max" + "_completion_tokens")
        for path in root.rglob("*.py"):
            if path.name == "trapi.py":
                continue
            source = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, source, str(path))


class ArtifactRootTests(unittest.TestCase):
    def test_cli_precedes_environment_and_default(self):
        with mock.patch.dict("os.environ", {"ORBIAS_ARTIFACT_ROOT": "/tmp/from-env"}):
            self.assertEqual(artifact_root(), Path("/tmp/from-env").resolve())
            self.assertEqual(artifact_root("/tmp/from-cli"), Path("/tmp/from-cli").resolve())


if __name__ == "__main__":
    unittest.main()
