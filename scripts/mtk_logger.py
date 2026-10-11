#!/usr/bin/env python3
"""Inspect/control ConnsysLog through MTK Logger's UI, preserving other log switches."""
import argparse
import json
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from capture import ROOT, adb_command

TOGGLE = "com.debug.loggerui:id/connsysLogStartStopToggleButton"

def toggle_info(xml):
    for node in ET.fromstring(xml).iter("node"):
        if node.get("resource-id") == TOGGLE:
            values = [int(value) for value in re.findall(r"\d+", node.get("bounds", ""))]
            if len(values) != 4:
                raise ValueError("Invalid toggle bounds")
            return {"checked": node.get("checked") == "true",
                    "x": (values[0] + values[2]) // 2,
                    "y": (values[1] + values[3]) // 2}
    raise ValueError("ConnsysLog toggle is absent from the current UI")

def run(*args):
    return subprocess.run(adb_command(*args), check=True, capture_output=True, timeout=30)

def inspect(out, label):
    remote = "/sdcard/scale-mtk-logger-ui.xml"
    run("shell", "uiautomator", "dump", remote)
    local = out / f"{label}.xml"
    run("pull", remote, str(local))
    return toggle_info(local.read_text())

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "start", "stop"])
    parser.add_argument("--out", type=Path, default=ROOT / "logs/mtk-cleanup")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    result = {}
    try:
        run("shell", "am", "start", "-n", "com.debug.loggerui/.MainActivity")
        time.sleep(0.6)
        before = inspect(args.out, "before")
        result["before"] = before
        if args.action != "status" and before["checked"] != (args.action == "start"):
            run("shell", "input", "tap", str(before["x"]), str(before["y"]))
            time.sleep(1)
            after = inspect(args.out, "after")
            result["after"] = after
            if after["checked"] != (args.action == "start"):
                raise RuntimeError(f"ConnsysLog did not reach the requested state: {args.action}")
        print(json.dumps(result), flush=True)
    finally:
        (args.out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        run("shell", "am", "start", "-n", "com.huawei.health/.MainActivity")

if __name__ == "__main__":
    main()
