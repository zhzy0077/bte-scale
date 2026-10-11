#!/usr/bin/env python3
"""Extract an AH100 ATT conversation with tshark and decode measurement values."""
import argparse
import datetime
import json
import struct
import subprocess
import uuid
from pathlib import Path
from decode_ah100 import decode_weight, decode_body

ROOT = Path(__file__).resolve().parents[1]


def first(value):
    return value[0] if isinstance(value, list) else value


def parse_epoch(value):
    # tshark versions serialize FT_ABSOLUTE_TIME as numeric epochs or ISO timestamps.
    value = first(value)
    try:
        return float(value)
    except ValueError:
        return datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def uuid_text(data):
    if len(data) == 2:
        return f"{int.from_bytes(data, 'little'):04x}"
    if len(data) == 16:
        return str(uuid.UUID(bytes=data[::-1]))
    raise ValueError("Unsupported UUID length")


def discover(packets):
    """Learn characteristic value/descriptor UUIDs from ATT discovery responses."""
    handles = {}
    pending = {}
    services = []
    for packet in packets:
        layers = packet['_source']['layers']
        raw_field = layers.get('btatt_raw')
        if not raw_field:
            continue
        raw = bytes.fromhex(raw_field[0])
        if not raw:
            continue
        channel = first(layers.get('bthci_acl', {}).get('bthci_acl.chandle', 'unknown'))
        op = raw[0]
        if op == 0x08 and len(raw) >= 7:
            pending[channel] = uuid_text(raw[5:])
        elif op == 0x09 and len(raw) >= 2 and pending.get(channel) == '2803':
            size = raw[1]
            if size not in (7, 21):
                continue
            for offset in range(2, len(raw) - size + 1, size):
                entry = raw[offset:offset + size]
                handle = int.from_bytes(entry[3:5], 'little')
                handles[handle] = uuid_text(entry[5:])
        elif op == 0x05 and len(raw) >= 2:
            size = {1: 4, 2: 18}.get(raw[1])
            if size:
                for offset in range(2, len(raw) - size + 1, size):
                    entry = raw[offset:offset + size]
                    handles[int.from_bytes(entry[:2], 'little')] = uuid_text(entry[2:])
        elif op == 0x11 and len(raw) >= 2:
            size = raw[1]
            if size not in (6, 20):
                continue
            for offset in range(2, len(raw) - size + 1, size):
                entry = raw[offset:offset + size]
                start, end = struct.unpack_from('<HH', entry)
                services.append({'start': start, 'end': end, 'uuid': uuid_text(entry[4:])})
    return handles, services


def rows(packets, handles, shift_hours):
    result = []
    names = {0x12: 'write_request', 0x52: 'write_command', 0x1b: 'notification',
             0x1d: 'indication', 0x1e: 'confirmation', 0x0a: 'read_request',
             0x0b: 'read_response', 0x13: 'write_response'}
    tz = datetime.timezone.utc
    for packet in packets:
        layers = packet['_source']['layers']
        field = layers.get('btatt_raw')
        if not field:
            continue
        raw = bytes.fromhex(field[0])
        if not raw:
            continue
        frame = layers['frame']
        epoch = parse_epoch(frame['frame.time_epoch'])
        actual = datetime.datetime.fromtimestamp(epoch, tz) + datetime.timedelta(hours=shift_hours)
        row = {'frame': int(first(frame['frame.number'])), 'hci_epoch': epoch,
               'phone_time_aligned': actual.isoformat(timespec='milliseconds'),
               'opcode': f'{raw[0]:02x}', 'action': names.get(raw[0], 'discovery_or_other'),
               'att_hex': raw.hex()}
        if raw[0] in (0x12, 0x52, 0x1b, 0x1d, 0x0a) and len(raw) >= 3:
            handle = int.from_bytes(raw[1:3], 'little')
            row['handle'] = f'{handle:04x}'
            row['uuid'] = handles.get(handle)
            value = raw[3:]
            if raw[0] != 0x0a:
                row['value_hex'] = value.hex()
            if raw[0] in (0x1b, 0x1d):
                decoder = {'2a9d': decode_weight, '2a9c': decode_body}.get(row['uuid'])
                if decoder:
                    try:
                        row['decoded'] = decoder(value)
                    except ValueError as error:
                        row['decode_error'] = str(error)
        result.append(row)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--device', required=True)
    parser.add_argument('--out', type=Path, default=ROOT / 'analysis/full-measurement')
    parser.add_argument('--time-shift-hours', type=float, default=0,
                        help='Vendor HCI timestamp correction relative to phone local time')
    args = parser.parse_args()
    try:
        device_bytes = bytes.fromhex(args.device.replace(':', ''))
    except ValueError:
        parser.error('Invalid Bluetooth address')
    if len(device_bytes) != 6:
        parser.error('Bluetooth address requires 6 bytes')
    args.out.mkdir(parents=True, exist_ok=True)
    tshark = 'tshark'
    display_filter = f'btatt && (bthci_acl.src.bd_addr == {args.device} || bthci_acl.dst.bd_addr == {args.device})'
    base = [tshark, '-r', str(args.capture), '-Y', display_filter]
    with (args.out / 'att.json').open('wb') as file:
        subprocess.run(base + ['-T', 'json', '-x'], stdout=file, check=True)
    packets = json.loads((args.out / 'att.json').read_text())
    if not packets:
        raise RuntimeError('No target ATT packets found')
    subprocess.run(base + ['-w', str(args.out / 'scale-att.pcapng')], check=True)
    handles, services = discover(packets)
    events = rows(packets, handles, args.time_shift_hours)
    measurements = [event for event in events if 'decoded' in event]
    summary = {'capture': str(args.capture), 'device': args.device,
               'time_shift_hours': args.time_shift_hours, 'att_packet_count': len(packets),
               'services': services, 'handles': {f'{key:04x}': value for key, value in sorted(handles.items())},
               'measurement_packet_count': len(measurements), 'measurements': measurements,
               'writes': [event for event in events if event['action'].startswith('write_') and 'value_hex' in event]}
    (args.out / 'events.json').write_text(json.dumps(events, indent=2) + '\n')
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    lines = ['frame\tphone_time_aligned\taction\thandle\tuuid\tvalue_hex\tdecoded']
    for event in events:
        if 'value_hex' in event or event['action'] == 'confirmation':
            fields = [event['frame'], event['phone_time_aligned'], event['action'],
                      event.get('handle', ''), event.get('uuid', ''), event.get('value_hex', ''),
                      json.dumps(event.get('decoded', {}), ensure_ascii=False)]
            lines.append('\t'.join(map(str, fields)))
    (args.out / 'values.tsv').write_text('\n'.join(lines) + '\n')
    print(f'{len(packets)} target ATT packets; {len(measurements)} decoded measurements')
    latest_by_kind = {event['decoded']['kind']: event for event in measurements}
    for event in latest_by_kind.values():
        print(json.dumps(event['decoded'], ensure_ascii=False))
    print(args.out)


if __name__ == '__main__':
    main()
