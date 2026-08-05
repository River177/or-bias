import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UnifiedCliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "orbias.cli", *args],
            cwd=ROOT,
            env={"PYTHONPATH": str(ROOT / "src")},
            check=True,
            capture_output=True,
            text=True,
        )

    def test_group_help_is_available(self):
        self.assertIn("translate", self.run_cli("--help").stdout)
        self.assertIn("finalize", self.run_cli("translate", "--help").stdout)

    def test_legacy_script_help_still_works(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "orbench_multimodel.py"), "--help"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("generate-full", completed.stdout)


if __name__ == "__main__":
    unittest.main()
