# BTE Scale

Python tools for collecting and decoding Huawei/Honor AH100 Bluetooth body-scale measurements, plus a portable implementation of the Chipsea visceral-fat calculation.

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

Supply your scale's Bluetooth address and profile explicitly. Disconnect competing BLE clients, wake the scale, and step onto it when the collector reports ready.

```sh
python scripts/collect_pc.py --address "$SCALE_ADDRESS" \
  --profile config/profile.local.json --out captures/session
python scripts/derive_vfr.py --result captures/session/result.json
```

The result includes weight, body-fat percentage, measurement timestamp, and profile. See [usage](docs/usage.md) for Android capture and analysis, and [protocol](docs/protocol.md) for encoding details.

## Tests

```sh
python -m unittest discover -s tests -v
```

## Data handling

Profiles, captures, screenshots, logs, APKs, native libraries, and generated analysis stay local and are ignored by Git. Captures can contain health data, Bluetooth addresses, and Android diagnostics. Tests use synthetic inputs.
