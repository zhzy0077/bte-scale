#!/usr/bin/env python3
"""Portable port of AH100's Chipsea getResistance -> getVFR calculation.

Recovered from libcsalgorithm.so SHA256
47a17e688d8122724452348bc61de71c0a9b75836b05f75468bc73b4b3b7ca42.
The profile gender is the scale's wire encoding (0 male, 1 female).
Algorithm gender is inverted (1 male, 0 female).
"""
import argparse
import datetime
import json
import math
import struct
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# coefficient[8 + algorithm_gender], columns H, W, age, transformed Z, bias.
VFR_COEFFICIENTS = ((-1651, 2628, 649, 24, 123445),
                    (-2675, 4200, 1462, 123, 139871))
# H, W, age, bias, denominator in the inverse body-fat regression.
RESISTANCE_COEFFICIENTS = ((0.3332, -0.7509, -0.0196, -22.7193, 0.0072),
                         (0.3315, -0.6216, -0.0183, -22.554, 0.0085))


def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def fma(a, b, c):
    if hasattr(math, 'fma'):
        return math.fma(a, b, c)
    # Exact binary inputs and one rounding, for Python versions before 3.13.
    return float(Fraction(a)*Fraction(b)+Fraction(c))


def fma32(a, b, c):
    return f32(fma(a, b, c))


def signed(value, bits):
    value &= (1 << bits)-1
    return value-(1 << bits) if value & (1 << (bits-1)) else value


def validate(height_cm, algorithm_gender, weight_kg, age):
    if algorithm_gender not in (0, 1):
        raise ValueError('Algorithm gender must be 0 or 1')
    if not math.isfinite(height_cm) or not 90 <= height_cm <= 220:
        raise ValueError('Algorithm height must be in 90..220 cm')
    if not math.isfinite(weight_kg) or not 20 <= weight_kg <= 150:
        raise ValueError('Algorithm weight must be in 20..150 kg')
    if type(age) is not int or not 10 <= age <= 99:
        raise ValueError('Algorithm age must be an integer in 10..99')


def get_resistance(height_cm, algorithm_gender, weight_kg, age, body_fat_percent):
    """Exact native getResistance result for the App's supported input domain."""
    validate(height_cm, algorithm_gender, weight_kg, age)
    if not math.isfinite(body_fat_percent):
        raise ValueError('Body fat must be finite')
    h, w, fat = map(f32, (height_cm, weight_kg, body_fat_percent))
    if fat <= 5 or fat >= 45:
        return 0
    mass = f32(w*f32(fat/f32(100)))
    ch, cw, ca, bias, denominator = RESISTANCE_COEFFICIENTS[algorithm_gender]
    x = fma(h, ch, mass)
    x = fma(w, cw, x)
    x = fma(float(age), ca, x)
    q = math.trunc((x+bias)/denominator)
    # Native truncates this intermediate before recovering R; preserve binary32.
    r = fma32(w, f32(4), f32(signed(q*6, 32)))
    r = f32(r/f32(10))
    return math.trunc(f32(r+f32(100)))


def quantize_vfr(raw):
    """Native half-level quantization: fractional tenth >5 selects .5."""
    tenth = math.trunc(raw*10)
    if tenth < 10:
        return 1.0
    if tenth > 589:
        return 59.0
    base, remainder = divmod(tenth, 10)
    return float(base)+(0.5 if remainder > 5 else 0.0)


def vfr_details(height_cm, algorithm_gender, weight_kg, age, resistance, year=2026):
    validate(height_cm, algorithm_gender, weight_kg, age)
    if type(resistance) is not int or not -(2**31) <= resistance < 2**31:
        raise ValueError('Resistance must be an int32')
    if type(year) is not int:
        raise ValueError('Year must be an integer')
    h, w = f32(height_cm), f32(weight_kg)
    # Two unusual early returns in the original JNI wrapper, before bia_calc.
    special = (age == year-1949 and
               ((algorithm_gender == 1 and h == 200 and 19 <= w <= 21) or
                (algorithm_gender == 0 and h == 100 and 99 <= w <= 101)))
    if special:
        return {'native_special_case': True, 'visceral_fat_level': h}
    value = f32(signed(resistance*10-1000, 32))
    transformed = math.trunc(f32(fma32(w, f32(-4), value)/f32(6)))
    h10 = signed(math.trunc(f32(h*f32(10))), 16)
    w10 = signed(math.trunc(f32(w*f32(10))), 16)
    z10 = signed(transformed*10, 16)
    ch, cw, ca, cz, bias = VFR_COEFFICIENTS[algorithm_gender]
    raw = ch*h10/10 + cw*w10/10
    raw = fma(float(ca), float(age), raw)
    raw += cz*z10/10
    raw = (raw+bias)/10000
    return {'native_special_case': False,
            'input_height_tenths': h10, 'input_weight_tenths': w10,
            'input_transformed_impedance_tenths': z10,
            'raw_regression': raw,
            'visceral_fat_level': f32(quantize_vfr(raw))}


def get_vfr(height_cm, algorithm_gender, weight_kg, age, resistance, year=2026):
    """Native getVFR result, including the JNI wrapper's special branches."""
    return vfr_details(height_cm, algorithm_gender, weight_kg, age, resistance,
                       year)['visceral_fat_level']


def derive(weight_kg, body_fat_percent, profile, year=2026):
    if not math.isfinite(body_fat_percent) or f32(body_fat_percent) <= 0.5:
        raise ValueError('App derived metrics require body fat above 0.5%')
    wire_gender = profile['gender']
    if type(wire_gender) is not int or wire_gender not in (0, 1):
        raise ValueError('Profile gender must use scale wire encoding 0 or 1')
    gender = 1-wire_gender
    height, age = profile['height_cm'], profile['age']
    resistance = get_resistance(height, gender, weight_kg, age, body_fat_percent)
    # eag.k() converts negative native resistance to -1 before wk is configured.
    used_resistance = max(resistance, -1)
    details = vfr_details(height, gender, weight_kg, age, used_resistance, year)
    level = details['visceral_fat_level']
    return {'weight_kg': weight_kg, 'body_fat_percent': body_fat_percent,
            'height_cm': height, 'age': age, 'scale_gender': wire_gender,
            'algorithm_gender': gender, 'algorithm_year': year,
            'algorithm_resistance': resistance,
            'resistance_used_for_vfr': used_resistance,
            **details,
            'app_visceral_fat_level': level if 1 <= level <= 59 else -1.0,
            'source': 'App Chipsea arithmetic port; derived from measurement and profile'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weight', type=float)
    parser.add_argument('--body-fat', type=float)
    parser.add_argument('--result', type=Path, help='Existing PC result.json')
    parser.add_argument('--profile', type=Path, help='Profile JSON; required unless the result includes a profile')
    parser.add_argument('--year', type=int, help='Calculation year; defaults to measurement year or current year')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    year = args.year
    profile = None
    if args.result:
        if args.weight is not None or args.body_fat is not None:
            parser.error('--result is exclusive with manual readings')
        result = json.loads(args.result.read_text())
        args.weight = result['weight_kg']
        args.body_fat = result['body_fat_percent']
        profile = result.get('profile')
        if year is None:
            timestamp = result.get('measurement_timestamp') or result.get('timestamp')
            if timestamp:
                year = datetime.datetime.fromisoformat(timestamp).year
    if args.weight is None or args.body_fat is None:
        parser.error('Supply --result, or both --weight and --body-fat')
    if year is None:
        year = datetime.datetime.now().year
    if args.profile is not None or profile is None:
        if args.profile is None:
            parser.error('Supply --profile when the result has no profile')
        profile = json.loads(args.profile.read_text())
    output = json.dumps(derive(args.weight, args.body_fat, profile, year), indent=2, ensure_ascii=False)+'\n'
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output)
    print(output, end='')


if __name__ == '__main__':
    main()
