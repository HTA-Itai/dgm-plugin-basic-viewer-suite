import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "applications/basic_viewer_data_builder/main.py"
BASE = Path("/home/itai/work/plugin/Input/GT2000/2D_Micron/extensions/20260824_024221_B78121PEB_4F190S_CF_v02_4_B3NM531_001")


class RealDatasetBuilderTests(unittest.TestCase):
    def test_build_2d_and_multi(self):
        if not BASE.exists():
            self.skipTest("Specified test data is not mounted")
        cases = [
            ("20260825153709_0001@2DMeasurement", "result_2D.csv", "B781_2D"),
            ("20260825172353_0001@MultiMeasurement", "result_Size_Area.csv", "B781_Multi"),
        ]
        for folder, target_csv, dataset_id in cases:
            with self.subTest(folder=folder):
                source = BASE / folder
                with tempfile.TemporaryDirectory() as tmp:
                    output = Path(tmp) / f"{dataset_id}.sqlite"
                    result = subprocess.run(
                        [os.environ.get("BUILDER_PYTHON", "python3"), str(BUILDER),
                         "--source", str(source), "--output", str(output),
                         "--dataset-id", dataset_id, "--target-csv", target_csv],
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(output.is_file())
