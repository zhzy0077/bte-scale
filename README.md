# BTE Scale

Python tools for **荣耀体脂秤 AH100 (Huawei/Honor AH100)**: collect and decode Bluetooth weight/body-fat measurements and calculate the app's derived visceral-fat level locally.

## How this was created

The tools were built by reverse-engineering the scale's Bluetooth communication and its Android companion app:

1. Capture Android Bluetooth HCI logs during a measurement and analyze ATT traffic with `tshark` to identify characteristic UUIDs, profile writes, measurement packets, and timestamps.
2. Implement that exchange in a Python/Bleak collector: set the scale's clock, subscribe to weight and body-composition indications, and send age, gender, and height after a positive weight reading.
3. Inspect the APK's DEX code and Chipsea `libcsalgorithm.so`, then port `getResistance` and `getVFR` to Python, preserving native floating-point rounding, quantization, and special cases. An optional AArch64 native oracle supports differential verification against the matching library.
4. Add synthetic packet fixtures and unit tests for decoding, collection behavior, capture utilities, and derived calculations.

See [protocol details](docs/protocol.md) and [research utilities](docs/usage.md#apk-research).

## Structure

- `scripts/`: BLE collector, packet decoders, HCI analyzer, Android capture utilities, and APK research tools.
- `tests/`: synthetic protocol fixtures and unit tests.
- `config/profile.example.json`: synthetic profile template.
- `docs/`: protocol and usage reference.

## Setup

Requires Python 3.10+. Direct BLE collection uses Linux BlueZ and a Bluetooth adapter. Install `tshark` for HCI analysis and Android platform-tools (`adb`) for Android capture. APK decompilation additionally uses an independently installed JADX.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp config/profile.example.json config/profile.local.json
```

Edit the local profile with the intended user's age, height in centimeters, and scale gender encoding (`0` male, `1` female).

## Collect a measurement

Find the AH100's Bluetooth address with `bluetoothctl scan on` while the scale is awake, then set it below. Supply your profile explicitly, disconnect competing BLE clients (including the phone app), wake the scale, and step onto it barefoot when the collector reports ready.

```sh
export SCALE_ADDRESS="<your scale's Bluetooth address>"
python scripts/collect_pc.py --address "$SCALE_ADDRESS" \
  --profile config/profile.local.json --out captures/session
python scripts/derive_vfr.py --result captures/session/result.json
```

The result includes weight, body-fat percentage, measurement timestamp, and profile. See [usage](docs/usage.md) for Android capture and analysis, and [protocol](docs/protocol.md) for encoding details.

## Continuous collection

The sibling `../workers` project now includes a native Rust/Tokio AH100 collector with automatic reconnection, JSONL storage, and optional notifications. See [worker setup and Fedora deployment](../workers/docs/scale.md). Enable it with `SCALE_ADDRESS` and `SCALE_PROFILE`; the Python tools here remain available for capture analysis and derived visceral fat.

## Tests

```sh
python -m unittest discover -s tests -v
```

## Data handling

Profiles, captures, screenshots, logs, APKs, native libraries, and generated analysis stay local and are ignored by Git. Captures can contain health data, Bluetooth addresses, and Android diagnostics. Tests use synthetic inputs.
