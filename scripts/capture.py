#!/usr/bin/env python3
"""Collect a timestamped ADB snapshot or measurement session."""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "com.huawei.health"

def adb_command(*args):
    cmd = ["adb"]
    if os.environ.get("ANDROID_SERIAL"):
        cmd += ["-s", os.environ["ANDROID_SERIAL"]]
    return cmd + list(args)

def save(out, name, *args, timeout=45):
    with (out / name).open("wb") as file:
        result = subprocess.run(adb_command(*args), stdout=file, stderr=subprocess.STDOUT, timeout=timeout)
    return result.returncode

def snapshot(out):
    save(out, "device.txt", "shell",
         "id; getprop ro.product.model; getprop ro.build.version.release; "
         "getprop ro.build.version.sdk; command -v su; "
         "getprop persist.bluetooth.btsnooplogmode; "
         "settings get global bluetooth_btsnoop_log_mode")
    save(out, "bluetooth.txt", "shell", "dumpsys", "bluetooth_manager")
    save(out, "activity.txt", "shell", "dumpsys", "activity", "activities")
    save(out, "package.txt", "shell", "dumpsys", "package", PACKAGE)
    save(out, "screen.png", "exec-out", "screencap", "-p")

def extract_hci(out):
    candidates = []
    for report in out.glob("*.zip"):
        with zipfile.ZipFile(report) as archive:
            for name in archive.namelist():
                lowered = name.lower()
                # MediaTek/realme stores HCI captures as .cfa/.cfa.curf.
                is_vendor_hci = "/bluetooth/logs/bthci/" in lowered and (
                    lowered.endswith(".cfa") or lowered.endswith(".cfa.curf"))
                if "btsnoop" in lowered or "btsnooz" in lowered or is_vendor_hci:
                    candidates.append(f"{report.name}: {name}")
                    if not name.endswith("/"):
                        # Flatten names and prefix with index to avoid path traversal/collisions.
                        dest = out / "hci" / f"{len(candidates):03d}-{Path(name).name}"
                        dest.parent.mkdir(exist_ok=True)
                        with archive.open(name) as src, dest.open("wb") as dst:
                            import shutil
                            shutil.copyfileobj(src, dst)
    (out / "hci-files.txt").write_text("\n".join(candidates) + "\n")
    print(f"HCI-related files: {len(candidates)}", flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["snapshot", "record", "bugreport", "extract"])
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--bugreport", action="store_true", help="Generate bugreport after recording")
    parser.add_argument("--stop-mtk", action="store_true", help="Stop ConnsysLog when recording finishes")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    if args.action == "extract" and args.out is None:
        parser.error("extract requires --out SESSION_DIRECTORY")
    out = args.out or ROOT / "captures" / datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True, exist_ok=True)
    print(out, flush=True)
    if args.action == "extract":
        extract_hci(out)
        return
    if args.action in ("snapshot", "record"):
        if args.action == "record":
            metadata = {"start": datetime.datetime.now().astimezone().isoformat(),
                        "seconds_requested": args.seconds, "package": PACKAGE}
            # Start logging before the initial snapshot; keep existing device logs intact.
            with (out / "logcat.txt").open("wb") as log:
                proc = subprocess.Popen(adb_command("logcat", "-v", "threadtime", "-T", "1"),
                                        stdout=log, stderr=subprocess.STDOUT)
                try:
                    start = time.monotonic()
                    snapshot(out)
                    print("Recording: wake the scale and complete a measurement.", flush=True)
                    # Creating SESSION_DIRECTORY/STOP ends a background session gracefully.
                    while time.monotonic() - start < args.seconds and not (out / "STOP").exists():
                        if proc.poll() is not None:
                            raise RuntimeError(f"adb logcat exited: {proc.returncode}")
                        time.sleep(0.5)
                except KeyboardInterrupt:
                    print("Stopping capture.", flush=True)
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                    metadata["end"] = datetime.datetime.now().astimezone().isoformat()
                    (out / "session.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
                    if args.stop_mtk:
                        # Close the native HCI writer before exporting; also runs on errors/timeouts.
                        save(out, "screen-measured.png", "exec-out", "screencap", "-p")
                        with (out / "mtk-stop-console.txt").open("wb") as console:
                            subprocess.run([sys.executable, str(ROOT / "scripts/mtk_logger.py"),
                                            "stop", "--out", str(out / "mtk-stop")],
                                           stdout=console, stderr=subprocess.STDOUT,
                                           timeout=90, check=True)
            snapshot(out_after(out))
        else:
            snapshot(out)
    if args.action == "bugreport" or args.bugreport:
        result = subprocess.run(adb_command("bugreport", str(out / "bugreport.zip")), timeout=600)
        result.check_returncode()
        extract_hci(out)

def out_after(out):
    directory = out / "after"
    directory.mkdir(exist_ok=True)
    return directory

if __name__ == "__main__":
    main()
