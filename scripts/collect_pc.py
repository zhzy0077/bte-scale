#!/usr/bin/env python3
"""Collect one AH100 weight/body-fat measurement directly through PC BLE."""
import argparse
import asyncio
import datetime as dt
import importlib.metadata
import json
import logging
from contextlib import asynccontextmanager
import signal
import subprocess
import sys
import time
from pathlib import Path
from bleak import BleakClient, BleakScanner
from bleak.exc import BleakDBusError, BleakError
from capture import ROOT, adb_command
from decode_ah100 import decode_weight, decode_body

BASE = "-0000-1000-8000-00805f9b34fb"
UUIDS = {name: "0000" + short + BASE for name, short in {
    "weight": "2a9d", "body": "2a9c", "time": "2a2b",
    "age": "2a80", "gender": "2a8c", "height": "2a8e"}.items()}


def current_time_value(now=None):
    now = now or dt.datetime.now()
    # Match the captured App's Calendar weekday convention (Sunday=1).
    weekday = (now.weekday() + 1) % 7 + 1
    return now.year.to_bytes(2, "little") + bytes([
        now.month, now.day, now.hour, now.minute, now.second, weekday, 0, 0])


def profile_values(profile):
    for key in ("age", "gender", "height_cm"):
        if type(profile.get(key)) is not int:
            raise ValueError(f"Profile {key} must be an integer")
    if not 0 <= profile["age"] <= 255:
        raise ValueError("Age must fit one byte")
    if profile["gender"] not in (0, 1):
        raise ValueError("Gender must be 0 or 1, matching the App's AH100 encoding")
    if not 1 <= profile["height_cm"] <= 255:
        raise ValueError("Height must be 1..255 cm for this App's AH100 encoding")
    return [("age", bytes([profile["age"]])),
            ("gender", bytes([profile["gender"]])),
            ("height", bytes([profile["height_cm"], 0]))]


def matching_measurements(weight, body):
    if weight is None or body is None:
        return False
    if weight["weight_kg"] <= 0 or body["body_fat_percent"] <= 0:
        return False
    return (not weight.get("timestamp") or not body.get("timestamp") or
            weight["timestamp"] == body["timestamp"])


class Journal:
    def __init__(self, out):
        self.out = out
        self.file = (out / "events.jsonl").open("a", encoding="utf-8")
        self.start = time.monotonic()

    def log(self, event, **fields):
        row = {"time": dt.datetime.now().astimezone().isoformat(),
               "elapsed_seconds": round(time.monotonic() - self.start, 3),
               "event": event, **fields}
        self.file.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.file.flush()
        print(json.dumps(row, ensure_ascii=False), flush=True)

    def close(self):
        self.file.close()


def phone_bluetooth(command):
    result = subprocess.run(adb_command("shell", "cmd", "bluetooth_manager", command),
                            capture_output=True, text=True, timeout=30, check=True)
    if "Success" not in result.stdout:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout.strip()


async def write_value(client, characteristic, value, journal):
    try:
        await client.write_gatt_char(characteristic, value, response=False)
        response = False
    except BleakDBusError as error:
        if error.dbus_error not in ("org.bluez.Error.NotPermitted", "org.bluez.Error.NotSupported"):
            raise
        # AH100 declares Write rather than WriteWithoutResponse for user data.
        journal.log("write_mode_fallback", uuid=characteristic.uuid, error=str(error))
        await client.write_gatt_char(characteristic, value, response=True)
        response = True
    journal.log("write", uuid=characteristic.uuid, hex=value.hex(), response=response)


@asynccontextmanager
async def connected_client(device, timeout, disconnected):
    client = BleakClient(device, timeout=timeout, disconnected_callback=disconnected)
    try:
        await client.connect()
    except TimeoutError as error:
        raise ConnectionError(f"BLE connection/service discovery timed out after {timeout}s") from error
    try:
        yield client
    finally:
        await client.disconnect()


async def collect(args, profile, journal):
    queue = asyncio.Queue()
    target = args.address.lower()
    advertisement = {}

    def match(device, adv):
        if device.address.lower() != target:
            return False
        advertisement.update(rssi=adv.rssi, local_name=adv.local_name,
                             service_uuids=adv.service_uuids,
                             manufacturer_data={str(key): value.hex() for key, value in adv.manufacturer_data.items()})
        return True

    journal.log("scanning", address=args.address, timeout=args.scan_seconds)
    device = await BleakScanner.find_device_by_filter(
        match, timeout=args.scan_seconds, bluez={"filters": {"Transport": "le"}})
    if device is None:
        raise TimeoutError("Scale did not advertise before the scan deadline")
    journal.log("found", address=device.address, name=device.name, advertisement=advertisement)

    def disconnected(client):
        queue.put_nowait(("disconnect", None))

    journal.log("connecting", timeout=args.connect_timeout)
    async with connected_client(device, args.connect_timeout, disconnected) as client:
        journal.log("connected", address=device.address)
        services = [{"uuid": service.uuid,
                     "characteristics": [{"uuid": char.uuid, "handle": char.handle,
                                            "properties": char.properties}
                                           for char in service.characteristics]}
                    for service in client.services]
        (args.out / "services.json").write_text(json.dumps(services, indent=2) + "\n")
        chars = {name: client.services.get_characteristic(uuid) for name, uuid in UUIDS.items()}
        missing = [name for name, char in chars.items() if char is None]
        if missing:
            raise RuntimeError(f"Required GATT characteristics missing: {missing}")

        def callback(kind):
            def receive(char, data):
                queue.put_nowait((kind, bytes(data)))
            return receive

        await write_value(client, chars["time"], current_time_value(), journal)
        await client.start_notify(chars["weight"], callback("weight"))
        await client.start_notify(chars["body"], callback("body"))
        journal.log("ready", message="PC connected and subscribed; step onto the scale now")
        (args.out / "READY").touch()
        deadline = time.monotonic() + args.measure_seconds
        weight = None
        body = None
        configured_weight = None
        while time.monotonic() < deadline:
            kind, data = await asyncio.wait_for(queue.get(), timeout=max(0.1, deadline - time.monotonic()))
            if kind == "disconnect":
                raise ConnectionError("Scale disconnected before a complete measurement")
            try:
                decoded = (decode_weight if kind == "weight" else decode_body)(data)
            except ValueError as error:
                journal.log("decode_error", kind=kind, hex=data.hex(), error=str(error))
                continue
            journal.log("measurement", kind=kind, uuid=UUIDS[kind], hex=data.hex(), decoded=decoded)
            if kind == "weight":
                weight = decoded
                body = None
                if decoded["weight_kg"] > 0 and data != configured_weight:
                    for name, value in profile_values(profile):
                        await write_value(client, chars[name], value, journal)
                        await asyncio.sleep(0.1)
                    configured_weight = data
                    journal.log("profile_sent")
            else:
                body = decoded
            if matching_measurements(weight, body):
                result = {"collected_at": dt.datetime.now().astimezone().isoformat(),
                          "transport": "PC BLE/BlueZ", "address": device.address,
                          "weight_kg": weight["weight_kg"],
                          "body_fat_percent": body["body_fat_percent"],
                          "measurement_timestamp": weight.get("timestamp"),
                          "weight_hex": weight["hex"], "body_hex": body["hex"],
                          "profile": profile}
                (args.out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
                journal.log("complete", result=result)
                return
        raise TimeoutError("No matching weight/body-fat measurement before the deadline")


async def collect_with_retries(args, profile, journal):
    scan_deadline = time.monotonic() + args.scan_seconds
    for attempt in range(1, args.attempts + 1):
        remaining = scan_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Total scan/reconnection window expired")
        attempt_args = argparse.Namespace(**{**vars(args), "scan_seconds": remaining})
        try:
            await collect(attempt_args, profile, journal)
            return
        except (BleakError, ConnectionError) as error:
            journal.log("connection_retry", attempt=attempt, error=str(error))
            if attempt == args.attempts:
                raise
            await asyncio.sleep(1)


def interrupt(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--scan-seconds", type=float, default=120)
    parser.add_argument("--measure-seconds", type=float, default=180)
    parser.add_argument("--connect-timeout", type=float, default=45)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--manage-phone-bluetooth", action="store_true",
                        help="Temporarily turn off phone Bluetooth via ADB; restore it on exit")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if min(args.scan_seconds, args.measure_seconds, args.connect_timeout) <= 0 or args.attempts < 1:
        parser.error("Timeouts and attempt count must be positive")
    if args.debug:
        logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
        logging.getLogger("bleak.backends.bluezdbus.client").setLevel(logging.DEBUG)
    profile = json.loads(args.profile.read_text())
    profile_values(profile)
    args.out = args.out or ROOT / "captures" / (dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-pc")
    args.out.mkdir(parents=True, exist_ok=True)
    journal = Journal(args.out)
    restore_phone = False
    failed = False
    signal.signal(signal.SIGTERM, interrupt)
    try:
        journal.log("start", bleak_version=importlib.metadata.version("bleak"), profile=profile)
        if args.manage_phone_bluetooth:
            state = subprocess.run(adb_command("shell", "settings", "get", "global", "bluetooth_on"),
                                   check=True, capture_output=True, text=True, timeout=15).stdout.strip()
            if state not in ("0", "1"):
                raise RuntimeError(f"Unknown phone Bluetooth state: {state}")
            if state == "1":
                restore_phone = True
                phone_bluetooth("disable")
                phone_bluetooth("wait-for-state:STATE_OFF")
                journal.log("phone_bluetooth_disabled")
        asyncio.run(collect_with_retries(args, profile, journal))
    except (Exception, KeyboardInterrupt) as error:
        failed = True
        journal.log("failed", error_type=type(error).__name__, error=str(error))
        (args.out / "error.json").write_text(json.dumps({"type": type(error).__name__, "error": str(error)}, indent=2) + "\n")
    finally:
        if restore_phone:
            try:
                phone_bluetooth("enable")
                phone_bluetooth("wait-for-state:STATE_ON")
                journal.log("phone_bluetooth_restored")
            except Exception as error:
                failed = True
                journal.log("phone_bluetooth_restore_failed", error=str(error))
        journal.close()
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
