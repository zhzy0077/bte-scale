import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import derive_vfr as vfr

# Synthetic inputs, unrelated to captured measurements or personal profiles.
PROFILE = {'height_cm': 170, 'age': 40, 'gender': 1}


class VFRTests(unittest.TestCase):
    def test_pipeline(self):
        result = vfr.derive(70, 20, PROFILE)
        resistance = vfr.get_resistance(170, 0, 70, 40, 20)
        self.assertEqual(result['algorithm_gender'], 0)
        self.assertEqual(result['algorithm_resistance'], resistance)
        self.assertEqual(result['visceral_fat_level'],
                         vfr.get_vfr(170, 0, 70, 40, max(resistance, -1)))

    def test_quantization(self):
        for raw, level in ((-5, 1), (1.5, 1), (1.6, 1.5),
                           (8.5999, 8), (8.6, 8.5), (100, 59)):
            self.assertEqual(vfr.quantize_vfr(raw), level)

    def test_resistance_limits(self):
        for fat in (0, 5, 45, 80):
            self.assertEqual(vfr.get_resistance(170, 0, 70, 40, fat), 0)

    def test_validation(self):
        for profile in ({**PROFILE, 'height_cm': 89}, {**PROFILE, 'age': True},
                        {**PROFILE, 'gender': 2}):
            with self.assertRaises(ValueError):
                vfr.derive(70, 20, profile)
        with self.assertRaises(ValueError):
            vfr.derive(70, math.nan, PROFILE)

    def test_saved_result_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            path.write_text(json.dumps({'weight_kg': 70, 'body_fat_percent': 20,
                                        'profile': PROFILE}))
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/derive_vfr.py'),
                                     '--result', str(path), '--year', '2025'],
                                    check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(result.stdout)['algorithm_year'], 2025)

    def test_manual_cli_requires_profile(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/derive_vfr.py'),
                                 '--weight', '70', '--body-fat', '20'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Supply --profile', result.stderr)


if __name__ == '__main__':
    unittest.main()
