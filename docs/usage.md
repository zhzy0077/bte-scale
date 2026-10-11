# Usage

## Android capture

Install ADB and authorize the phone. For multiple phones set `ANDROID_SERIAL`. Enable Bluetooth HCI logging using the phone's supported settings before recording.

```sh
python scripts/capture.py record --seconds 180 --bugreport
python scripts/capture.py extract --out captures/session
python scripts/analyze_hci.py captures/session/hci/capture.log \
  --device "$SCALE_ADDRESS" --out analysis/session
```

The analyzer uses system `tshark`, discovers ATT handle/UUID mappings, and emits JSON, TSV, and filtered packets. Output timestamps use UTC. `--time-shift-hours` applies an explicit vendor timestamp correction; its default is zero.

For phones with the MediaTek `com.debug.loggerui` app, `scripts/mtk_logger.py` provides `status`, `start`, and `stop` actions. `capture.py --stop-mtk` stops that logger after recording. The utility discovers UI toggle coordinates dynamically.

## PC capture with HCI diagnostics

```sh
python scripts/run_pc_capture.py --out captures/session --hci --adapter hci0 -- \
  --address "$SCALE_ADDRESS" --profile config/profile.local.json
```

HCI recording requires BlueZ `btmon` and configured `sudo -n` access. The supervisor uses the current Python interpreter. `collect_pc.py --manage-phone-bluetooth` temporarily disables the authorized ADB phone's Bluetooth and restores its previous enabled state on exit.

## Offline decoding and derived metrics

```sh
python scripts/decode_ah100.py weight 02b036e90701010c0000
python scripts/derive_vfr.py --weight 70 --body-fat 20 \
  --profile config/profile.local.json
```

These example readings are synthetic. The derived calculation accepts height 90–220 cm, weight 20–150 kg, and age 10–99. Supply `--year` to reproduce a particular calculation year.

## APK research

Supply your own APK to `scripts/index_apk.py`; generated indexes go under `analysis/`. `scripts/cs_native_oracle.py` is an optional AArch64 differential-testing helper that expects an extracted `analysis/native/libcsalgorithm.so` from the matching app version.
