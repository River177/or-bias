import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "unify_orbench.py"


class UnifiedORBenchTests(unittest.TestCase):
    def test_cli_converts_frozen_manifest_to_shared_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "manifest.jsonl"
            output = root / "orbench.jsonl"
            rows = [
                {
                    "prompt_id": "orbench-1",
                    "source_index": 0,
                    "source_language": "en",
                    "prompt": "A benign prompt",
                    "category": "privacy",
                },
                {
                    "prompt_id": "orbench-2",
                    "source_index": 1,
                    "source_language": "en",
                    "prompt": "Another benign prompt",
                    "category": "deception",
                },
            ]
            with source.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                    "--expected-rows",
                    "2",
                ],
                check=True,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )

            converted = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(len(converted), 2)
            self.assertEqual(converted[0]["record_id"], "orbench-1")
            self.assertEqual(converted[0]["dataset"], "orbench")
            self.assertEqual(converted[0]["dataset_variant"], "hard_full_snapshot")
            self.assertEqual(converted[0]["benchmark_group"], "primary_orbench")
            self.assertEqual(converted[0]["usage"], "evaluation")
            self.assertEqual(converted[0]["language"], "en")
            self.assertTrue(converted[0]["strict_benign"])
            self.assertEqual(converted[0]["category"], "privacy")
            self.assertEqual(converted[0]["metadata"]["source_index"], 0)

            manifest = json.loads(
                output.with_name("orbench_manifest.json").read_text()
            )
            self.assertEqual(manifest["rows"], 2)
            self.assertEqual(manifest["categories"], {"deception": 1, "privacy": 1})


if __name__ == "__main__":
    unittest.main()
