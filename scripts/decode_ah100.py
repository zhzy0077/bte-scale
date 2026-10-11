#!/usr/bin/env python3
"""Decode AH100 measurement values following Huawei Health's dri parser.

Input is a characteristic value, excluding ATT opcode and handle.
Source: analysis/jadx-scale/sources/defpackage/dri.java.
"""
import argparse
import datetime
import json
import struct
from decimal import Decimal

def timestamp(data, offset):
    if len(data) < offset + 7:
        raise ValueError("Truncated timestamp")
    year = struct.unpack_from("<H", data, offset)[0]
    return datetime.datetime(year, *data[offset + 2:offset + 7]).isoformat(sep=" ")

def decode_weight(data):
    if len(data) < 3:
        raise ValueError("Weight value requires at least 3 bytes")
    flags = data[0]
    raw = struct.unpack_from("<H", data, 1)[0]
    if flags & 1:
        kg = Decimal(raw) * Decimal("0.01") * Decimal("0.4535924")
    else:
        kg = Decimal(raw) * Decimal("0.005")
    result = {"kind": "weight", "flags": flags, "raw_weight": raw,
              "weight_kg": float(kg), "hex": data.hex()}
    if flags & 2:
        result["timestamp"] = timestamp(data, 3)
    return result

def decode_body(data):
    # The App's AH100 body-composition parser accepts exactly 11 bytes.
    if len(data) != 11:
        raise ValueError("AH100 body-composition value requires exactly 11 bytes")
    flags, raw = struct.unpack_from("<HH", data)
    result = {"kind": "body", "flags": flags, "raw_body_fat": raw,
              "body_fat_percent": raw / 10.0, "hex": data.hex()}
    if flags & 2:
        result["timestamp"] = timestamp(data, 4)
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["weight", "body"])
    parser.add_argument("hex_value", help="Hexadecimal characteristic bytes; colons/spaces allowed")
    args = parser.parse_args()
    try:
        data = bytes.fromhex(args.hex_value.replace(":", ""))
        result = (decode_weight if args.kind == "weight" else decode_body)(data)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
