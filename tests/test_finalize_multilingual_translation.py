import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import finalize_multilingual_translation as finalizer  # noqa: E402


class FinalizeMultilingualTranslationTests(unittest.TestCase):
    def test_exports_only_all_language_strict_common_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path = root / "source.jsonl"
            source_rows = []
            for record_id in ("a", "b"):
                prompt = f"Prompt {record_id}"
                digest = __import__("hashlib").sha256(prompt.encode()).hexdigest()
                source_rows.append(
                    {
                        "record_id": record_id,
                        "prompt": prompt,
                        "prompt_sha256": digest,
                        "usage": "evaluation",
                        "strict_benign": True,
                    }
                )
            source_path.write_text(
                "".join(json.dumps(row) + "\n" for row in source_rows), encoding="utf-8"
            )
            artifact_root = root / "artifacts"
            base = artifact_root / "sample"
            base.mkdir(parents=True)
            translations = []
            judgments = []
            for source in source_rows:
                for language in ("zh", "sw"):
                    translations.append(
                        {
                            "record_id": source["record_id"],
                            "language": language,
                            "translated_prompt": f"{source['record_id']}-{language}",
                            "source_prompt_sha256": source["prompt_sha256"],
                        }
                    )
                    judgments.append(
                        {
                            "record_id": source["record_id"],
                            "language": language,
                            "strict_keep": source["record_id"] == "a",
                            "decision": "keep" if source["record_id"] == "a" else "repair",
                        }
                    )
            (base / "translations.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in translations), encoding="utf-8"
            )
            (base / "translation_judgments.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in judgments), encoding="utf-8"
            )
            config = {"languages": ["en", "zh", "sw"], "prompt_version": "v1"}
            report = finalizer.finalize_dataset(
                spec={"key": "sample", "path": str(source_path)},
                config=config,
                config_path=root / "config.json",
                artifact_root=artifact_root,
                output_root=root / "final",
                approved=set(),
            )
            self.assertEqual(report["strict_common_rows"], 1)
            output = finalizer.read_jsonl(root / "final" / "datasets" / "sample.jsonl")
            self.assertEqual([row["record_id"] for row in output], ["a"])
            self.assertEqual(output[0]["prompts"], {"en": "Prompt a", "zh": "a-zh", "sw": "a-sw"})


if __name__ == "__main__":
    unittest.main()
