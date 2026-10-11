import asyncio
import contextlib
import datetime
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
try:
    import collect_pc as pc
except ModuleNotFoundError as error:
    if error.name not in ('bleak', 'dbus_fast'):
        raise
    pc = None


@unittest.skipIf(pc is None, 'Run PC BLE tests with the project .venv')
class PCTests(unittest.TestCase):
    def test_time_value_matches_app(self):
        now = datetime.datetime(2025, 1, 1, 15, 1, 14)
        self.assertEqual(pc.current_time_value(now).hex(), 'e90701010f010e040000')

    def test_profile_values(self):
        values = pc.profile_values({'age': 30, 'gender': 0, 'height_cm': 170})
        self.assertEqual([(key, value.hex()) for key, value in values],
                         [('age', '1e'), ('gender', '00'), ('height', 'aa00')])
        with self.assertRaises(ValueError):
            pc.profile_values({'age': 30, 'gender': 0, 'height_cm': 300})

    def test_matching_measurements(self):
        weight = {'weight_kg': 70.0, 'timestamp': 'same'}
        body = {'body_fat_percent': 20.0, 'timestamp': 'same'}
        self.assertTrue(pc.matching_measurements(weight, body))
        self.assertFalse(pc.matching_measurements(weight, {**body, 'timestamp': 'other'}))
        self.assertFalse(pc.matching_measurements(weight, {**body, 'body_fat_percent': 0}))

    def test_synthetic_measurement_pair(self):
        weight = pc.decode_weight(bytes.fromhex('02b036e9070101110828'))
        body = pc.decode_body(bytes.fromhex('0200c800e9070101110828'))
        self.assertEqual(weight['weight_kg'], 70.0)
        self.assertEqual(body['body_fat_percent'], 20.0)
        self.assertEqual(weight['timestamp'], '2025-01-01 17:08:40')
        self.assertTrue(pc.matching_measurements(weight, body))
        partial_weight = pc.decode_weight(bytes.fromhex('022003e9070101110818'))
        incomplete_body = pc.decode_body(bytes.fromhex('02000000e9070101110818'))
        self.assertFalse(pc.matching_measurements(partial_weight, incomplete_body))

    def test_write_command_fallback(self):
        error = pc.BleakDBusError('org.bluez.Error.NotPermitted', ['command unsupported'])
        client = SimpleNamespace(write_gatt_char=AsyncMock(side_effect=[error, None]))
        journal = SimpleNamespace(log=lambda *args, **kwargs: None)
        char = SimpleNamespace(uuid=pc.UUIDS['age'])
        asyncio.run(pc.write_value(client, char, b'\x1e', journal))
        calls = client.write_gatt_char.await_args_list
        self.assertFalse(calls[0].kwargs['response'])
        self.assertTrue(calls[1].kwargs['response'])

    def test_connect_timeout_is_retryable(self):
        client = SimpleNamespace(connect=AsyncMock(side_effect=TimeoutError()), disconnect=AsyncMock())
        async def run():
            with patch.object(pc, 'BleakClient', return_value=client):
                with self.assertRaisesRegex(ConnectionError, 'timed out'):
                    async with pc.connected_client(None, 45, None):
                        self.fail('Connection unexpectedly succeeded')
        asyncio.run(run())

    def test_complete_mock_session(self):
        written = []
        subscriptions = {}
        chars = {key: SimpleNamespace(uuid=value, handle=i, properties=['read', 'write', 'indicate'])
                 for i, (key, value) in enumerate(pc.UUIDS.items(), start=1)}
        service = SimpleNamespace(uuid='service', characteristics=list(chars.values()))

        class Services:
            def __iter__(self):
                return iter([service])
            def get_characteristic(self, uuid):
                return next(char for char in chars.values() if char.uuid == uuid)

        class Client:
            services = Services()
            async def connect(self):
                return True
            async def disconnect(self):
                return True
            async def write_gatt_char(self, char, data, response):
                written.append((char.uuid, bytes(data), response))
            async def start_notify(self, char, callback):
                subscriptions[char.uuid] = callback
                if char.uuid == pc.UUIDS['body']:
                    subscriptions[pc.UUIDS['weight']](chars['weight'], bytes.fromhex('02b036e90701010f0114'))
                    callback(chars['body'], bytes.fromhex('0200c800e90701010f0114'))

        device = SimpleNamespace(address='02:00:00:00:00:01', name='AH100')
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            out = Path(tmp)
            args = SimpleNamespace(address=device.address, scan_seconds=1, connect_timeout=1,
                                   measure_seconds=2, out=out)
            journal = pc.Journal(out)
            try:
                with patch.object(pc.BleakScanner, 'find_device_by_filter', AsyncMock(return_value=device)), \
                     patch.object(pc, 'BleakClient', return_value=Client()):
                    asyncio.run(pc.collect(args, {'age': 30, 'gender': 0, 'height_cm': 170}, journal))
            finally:
                journal.close()
            result = json.loads((out / 'result.json').read_text())
            self.assertEqual(result['weight_kg'], 70.0)
            self.assertEqual(result['body_fat_percent'], 20.0)
            self.assertTrue((out / 'READY').exists())
            self.assertEqual([data.hex() for _, data, _ in written[1:]], ['1e', '00', 'aa00'])
            self.assertTrue(all(not response for _, _, response in written))


if __name__ == '__main__':
    unittest.main()
