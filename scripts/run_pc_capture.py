#!/usr/bin/env python3
"""Supervise PC collection and a bounded local btmon diagnostic recording."""
import argparse
import json
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def interrupt(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--adapter', default='hci0', help='BlueZ HCI adapter for btmon')
    parser.add_argument('--hci', action='store_true', help='Record local HCI with sudo -n btmon')
    parser.add_argument('collector_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    extra = args.collector_args
    if extra[:1] == ['--']:
        extra = extra[1:]
    args.out.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, interrupt)
    monitor = None
    collector = None
    code = 1
    with (args.out / 'console.txt').open('ab') as console, (args.out / 'btmon.txt').open('ab') as hci_console:
        try:
            if args.hci:
                capture = args.out / 'pc-hci.btsnoop'
                capture.touch()
                monitor = subprocess.Popen(['sudo', '-n', 'btmon', '-i', args.adapter, '-T', '-P', '-w', str(capture)],
                                           stdout=hci_console, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL, start_new_session=True)
            collector = subprocess.Popen([sys.executable, '-u', str(ROOT / 'scripts/collect_pc.py'),
                                          '--out', str(args.out), *extra],
                                         stdout=console, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, start_new_session=True)
            (args.out / 'collector.pid').write_text(str(collector.pid) + '\n')
            (args.out / 'supervisor.json').write_text(json.dumps({
                'collector_pid': collector.pid, 'monitor_pid': monitor.pid if monitor else None}, indent=2) + '\n')
            code = collector.wait()
        except KeyboardInterrupt:
            code = 130
        finally:
            if collector is not None and collector.poll() is None:
                collector.terminate()
                try:
                    collector.wait(timeout=65)
                except subprocess.TimeoutExpired:
                    console.write(b'Collector did not stop; phone restoration needs checking.\n')
            if monitor is not None and monitor.poll() is None:
                # Each monitor owns a dedicated session/process group.
                subprocess.run(['sudo', '-n', 'kill', '-INT', '--', f'-{monitor.pid}'],
                               stdout=hci_console, stderr=subprocess.STDOUT, timeout=10)
                try:
                    monitor.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    subprocess.run(['sudo', '-n', 'kill', '-TERM', '--', f'-{monitor.pid}'], timeout=10)
            (args.out / 'exit-code.txt').write_text(str(code) + '\n')
    return code


if __name__ == '__main__':
    sys.exit(main())
