# AH100 protocol

The collector discovers characteristics by UUID rather than relying on fixed ATT handles.

| UUID | Purpose | Encoding |
| --- | --- | --- |
| `2a9d` | Weight indication | One-byte flags, little-endian uint16 weight, optional timestamp |
| `2a9c` | Body-composition indication | Two-byte flags, little-endian uint16 body fat, timestamp; 11 bytes total |
| `2a2b` | Current time | Little-endian year followed by month, day, hour, minute, second, weekday, fractions, adjustment |
| `2a80` | Age | One byte |
| `2a8c` | Gender | `0` male, `1` female |
| `2a8e` | Height | Centimeters followed by a zero byte |

Weight units are 0.005 kg when flag bit 0 is clear; imperial readings use 0.01 lb, converted to kilograms. Flag bit 1 indicates a timestamp: uint16 year and five bytes for month, day, hour, minute, second. Body-fat values use tenths of a percent.

The collection sequence sets time, subscribes to weight and body composition, and writes the profile after a positive weight reading. A complete result requires positive weight and body fat with matching timestamps when both are present. Unsupported write-command operations fall back to write requests.

## Derived visceral fat

`derive_vfr.py` ports the app's Chipsea `getResistance` and `getVFR` arithmetic. It inverts the scale gender encoding for the algorithm, preserves binary32 intermediate rounding, and applies native half-level quantization and clipping to 1–59. The level is computed from body fat, weight, and profile inputs.

The implementation retains native special-case branches and the app's invalid-level handling. Coefficients and native-library version hash are documented in the source.
