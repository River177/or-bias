import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "unify_external_overrefusal.py"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


class UnifiedExternalDatasetTests(unittest.TestCase):
    def test_cli_preserves_provenance_and_builds_filtered_views(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            selected = root / "selected"
            output = root / "unified"
            rows = [
                {
                    "selection_id": "falsereject-all:train:1",
                    "dataset": "falsereject-all",
                    "source_split": "train",
                    "source_file": "falsereject/train.jsonl",
                    "source_id": "1",
                    "prompt": "Training prompt",
                    "prompt_sha256": "a",
                    "source_label": "benign",
                    "category": "privacy",
                    "evaluation_role": "benign",
                    "primary_benign_eligible": True,
                    "metadata": {"original": 1},
                },
                {
                    "selection_id": "bio-overrefusal-all:full:1",
                    "dataset": "bio-overrefusal-all",
                    "source_split": "full",
                    "source_file": "bio/queries.jsonl",
                    "source_id": "1",
                    "prompt": "Ambiguous bio prompt",
                    "prompt_sha256": "b",
                    "source_label": "ambiguous",
                    "category": "virology",
                    "evaluation_role": "ambiguous",
                    "primary_benign_eligible": False,
                    "metadata": {"tier": 5},
                },
                {
                    "selection_id": "xstest-safe:test:1",
                    "dataset": "xstest-safe",
                    "source_split": "test",
                    "source_file": "xstest.csv",
                    "source_id": "1",
                    "prompt": "Evaluation prompt",
                    "prompt_sha256": "c",
                    "source_label": "safe",
                    "category": "figurative_language",
                    "evaluation_role": "benign",
                    "primary_benign_eligible": True,
                    "metadata": {},
                },
                {
                    "selection_id": "xstest-safe:test:2",
                    "dataset": "xstest-safe",
                    "source_split": "test",
                    "source_file": "xstest.csv",
                    "source_id": "2",
                    "prompt": "Evaluation prompt!",
                    "prompt_sha256": "d",
                    "source_label": "safe",
                    "category": "figurative_language",
                    "evaluation_role": "benign",
                    "primary_benign_eligible": True,
                    "metadata": {},
                },
            ]
            write_jsonl(selected / "all_selected.jsonl", rows)
            exclusions = root / "exclusions.jsonl"
            write_jsonl(
                exclusions,
                [
                    {
                        "selection_id": "xstest-safe:test:2",
                        "duplicate_of": "xstest-safe:test:1",
                    }
                ],
            )

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(selected / "all_selected.jsonl"),
                    "--duplicate-exclusions",
                    str(exclusions),
                    "--output-dir",
                    str(output),
                ],
                check=True,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )

            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["counts"]["all_rows"], 4)
            self.assertEqual(manifest["counts"]["canonical_unique"], 3)
            self.assertEqual(manifest["counts"]["strict_benign"], 2)
            self.assertEqual(manifest["counts"]["evaluation_strict_benign"], 1)

            canonical = [
                json.loads(line)
                for line in (output / "canonical_unique.jsonl").read_text().splitlines()
            ]
            training = next(row for row in canonical if row["dataset"] == "falsereject")
            self.assertEqual(training["usage"], "training")
            self.assertEqual(training["language"], "en")
            self.assertEqual(training["benchmark_group"], "general_fixed")
            self.assertEqual(training["metadata"], {"original": 1})
            self.assertTrue(training["strict_benign"])

            all_rows = [
                json.loads(line)
                for line in (output / "all_rows.jsonl").read_text().splitlines()
            ]
            duplicate = next(row for row in all_rows if row["source_id"] == "2")
            self.assertFalse(duplicate["include_in_canonical"])
            self.assertEqual(duplicate["duplicate_of"], "xstest-safe:test:1")


if __name__ == "__main__":
    unittest.main()
