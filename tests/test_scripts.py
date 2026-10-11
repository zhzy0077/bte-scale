import importlib.util
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded

index = module("index_apk")
capture = module("capture")
decode = module("decode_ah100")
mtk = module("mtk_logger")
hci = module("analyze_hci")

class ScriptsTests(unittest.TestCase):
    def test_minimal_dex(self):
        data = bytearray(120)
        data[:8] = b"dex\n035\0"
        struct.pack_into("<II", data, 56, 1, 112)
        struct.pack_into("<II", data, 64, 1, 116)
        struct.pack_into("<II", data, 96, 1, 120)
        struct.pack_into("<I", data, 116, 0)
        data.extend(bytes(32))
        name = b"Ltest/AH100;"
        struct.pack_into("<I", data, 112, len(data))
        data.extend(bytes([len(name)]) + name + b"\0")
        classes, strings = index.dex_index(data)
        self.assertEqual(classes, ["Ltest/AH100;"])
        self.assertEqual(strings, classes)

    def test_invalid_dex(self):
        with self.assertRaises(ValueError):
            index.dex_index(b"invalid")

    def test_synthetic_measurement_values(self):
        weight = decode.decode_weight(bytes.fromhex("02b036e90701010f0114"))
        body = decode.decode_body(bytes.fromhex("0200c800e90701010f0114"))
        self.assertEqual(weight["weight_kg"], 70.0)
        self.assertEqual(body["body_fat_percent"], 20.0)
        self.assertEqual(weight["timestamp"], "2025-01-01 15:01:20")

    def test_tshark_time_serialization(self):
        self.assertEqual(hci.parse_epoch("0"), 0)
        self.assertEqual(hci.parse_epoch("1970-01-01T00:00:00.000000000Z"), 0)

    def test_att_discovery(self):
        def packet(raw):
            return {"_source": {"layers": {
                "bthci_acl": {"bthci_acl.chandle": "0x0201"},
                "btatt_raw": [raw]}}}
        packets = [packet("080100ffff0328"), packet("09071a00201b009d2a"),
                   packet("05011c000229"), packet("110617001c001d18")]
        handles, services = hci.discover(packets)
        self.assertEqual(handles, {27: "2a9d", 28: "2902"})
        self.assertEqual(services, [{"start": 23, "end": 28, "uuid": "181d"}])

    def test_synthetic_indication_row(self):
        packet = {"_source": {"layers": {
            "frame": {"frame.number": "837",
                      "frame.time_epoch": "2025-01-01T15:01:25.399004000Z"},
            "btatt_raw": ["1d1b0002b036e90701010f0114"]}}}
        rows = hci.rows([packet], {27: "2a9d"}, -8)
        self.assertEqual(rows[0]["phone_time_aligned"], "2025-01-01T07:01:25.399+00:00")
        self.assertEqual(rows[0]["decoded"]["weight_kg"], 70.0)
        self.assertEqual(rows[0]["action"], "indication")

    def test_mtk_toggle(self):
        xml = ('<hierarchy><node resource-id="' + mtk.TOGGLE +
               '" checked="true" bounds="[175,1662][266,1753]"/></hierarchy>')
        self.assertEqual(mtk.toggle_info(xml), {"checked": True, "x": 220, "y": 1707})
        with self.assertRaises(ValueError):
            mtk.toggle_info("<hierarchy/>")

    def test_adb_serial(self):
        with patch.dict(os.environ, {"ANDROID_SERIAL": "test-device"}):
            self.assertEqual(capture.adb_command("devices"),
                             ["adb", "-s", "test-device", "devices"])

    def test_synthetic_weight(self):
        # Synthetic protocol fixture.
        result = decode.decode_weight(bytes.fromhex("02d836e90701010e242c"))
        self.assertEqual(result["weight_kg"], 70.2)
        self.assertEqual(result["timestamp"], "2025-01-01 14:36:44")

    def test_synthetic_body_fat(self):
        result = decode.decode_body(bytes.fromhex("0200c800e90701010e2430"))
        self.assertEqual(result["body_fat_percent"], 20.0)
        self.assertEqual(result["timestamp"], "2025-01-01 14:36:48")

    def test_measurement_truncation(self):
        for function, data in [(decode.decode_weight, b"\x02\x01"),
                               (decode.decode_weight, b"\x02\x01\x00"),
                               (decode.decode_body, bytes(10))]:
            with self.subTest(function=function.__name__, data=data):
                with self.assertRaises(ValueError):
                    function(data)

    def test_extract_hci_flattens_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with zipfile.ZipFile(out / "bugreport.zip", "w") as archive:
                archive.writestr("FS/data/misc/bluetooth/logs/btsnoop_hci.log", b"btsnoop\0")
                archive.writestr("../../btsnoop_hci.log", b"second")
                archive.writestr("bugreport.txt", b"unrelated")
                archive.writestr("FS/data/misc/bluetooth/logs/bthci/current.cfa.curf", b"vendor")
            capture.extract_hci(out)
            files = sorted((out / "hci").iterdir())
            self.assertEqual(len(files), 3)
            self.assertEqual(files[0].read_bytes(), b"btsnoop\0")
            self.assertEqual(files[1].read_bytes(), b"second")
            self.assertEqual(files[2].read_bytes(), b"vendor")
            self.assertTrue(all(file.parent == out / "hci" for file in files))

if __name__ == "__main__":
    unittest.main()
