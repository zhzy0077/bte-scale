#!/usr/bin/env python3
"""Index DEX classes and scale-related strings without installing dependencies."""
import argparse
import hashlib
import re
import struct
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"ah100|bodyfat|body.fat|bodycomposition|body.composition|scale|weight", re.I)

def dex_index(data):
    if not data.startswith(b"dex\n"):
        raise ValueError("Expected a standard DEX file")
    def u32(offset):
        return struct.unpack_from("<I", data, offset)[0]
    strings = []
    for i in range(u32(56)):
        pos = u32(u32(60) + i * 4)
        # Skip the ULEB128 UTF-16 length, then read MUTF-8 bytes.
        while data[pos] & 0x80:
            pos += 1
        pos += 1
        end = data.index(0, pos)
        strings.append(data[pos:end].decode("utf-8", errors="replace"))
    types = [strings[u32(u32(68) + i * 4)] for i in range(u32(64))]
    classes = [types[u32(u32(100) + i * 32)] for i in range(u32(96))]
    return classes, strings

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", nargs="?", type=Path, default=ROOT / "apk/base.apk")
    parser.add_argument("--extract-matching-dex", action="store_true")
    args = parser.parse_args()
    out = ROOT / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    class_rows, matched_classes, matched_strings = [], [], []
    with zipfile.ZipFile(args.apk) as archive:
        names = archive.namelist()
        (out / "apk-entries.txt").write_text("\n".join(names) + "\n")
        for name in names:
            if not re.fullmatch(r"classes\d*\.dex", name):
                continue
            data = archive.read(name)
            classes, strings = dex_index(data)
            class_rows.extend(f"{name}\t{c}" for c in classes)
            hits = [c for c in classes if PATTERN.search(c)]
            matched_classes.extend(f"{name}\t{c}" for c in hits)
            matched_strings.extend(f"{name}\t{s!r}" for s in strings if PATTERN.search(s))
            if hits and args.extract_matching_dex:
                target = ROOT / "apk/dex" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
    for name, rows in [("classes.tsv", class_rows), ("scale-classes.tsv", matched_classes),
                       ("scale-strings.tsv", matched_strings)]:
        (out / name).write_text("\n".join(rows) + "\n", encoding="utf-8")
    with args.apk.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    (out / "apk.sha256").write_text(f"{digest}  {args.apk.name}\n")
    print(f"{len(class_rows)} classes; {len(matched_classes)} candidates; {len(matched_strings)} strings")
    print(out)

if __name__ == "__main__":
    main()
