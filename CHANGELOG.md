# Changelog

This project follows [Semantic Versioning](https://semver.org/).

## [0.1.1] (2026-11-19)
Maintenance release from an Opus red-team round (no High finding; 4 Medium, 8 Low). No change to the plugin name, licence or `allowed-tools`. Each fix has a regression test (19 new, 59 in all).

Fixed
- M1 `key_decontam`: the pixel limit is now enforced before decoding (`OPENCV_IO_MAX_IMAGE_PIXELS` is set before OpenCV loads, and a PNG header is checked first). The limit is lowered from 60 to 25 megapixels. A 256-megapixel, 746 KB PNG used to allocate about 1.5 GB.
- M2 `duck_curve`: sample rates outside 4000..384000 Hz are refused, and the curve is capped at 6 hours of frames, both before any large allocation. A 50 KB WAV at 1 Hz used to produce 12 million frames and 683 MB.
- M3 all tools: ffmpeg is never taken from the current folder (`NoDefaultCurrentDirectoryInExePath` is set, and a result inside the current folder is refused). Applies to `blank_beats` and `keyframe-doctor`.
- M4 `duck_curve --force` replaces only an empty file or a JSON array of numbers; settings or other JSON files are refused even with `--force`. The skills now say: never pass `--force` unless the user explicitly asks. `allowed-tools` is unchanged.
- L1 usage errors exit 1 instead of argparse's 2 (2 means "finding"); `--help` still exits 0. The skills put `--` before the paths.
- L2 every float option must be finite (`nan` and `inf` refused with a clear message); `duck_curve` writes JSON with `allow_nan=False`.
- L3 outputs are created exclusively and never written through a symbolic link; with `--force` links and hard links are refused.
- L4 (partly) `blank_beats` runs ffmpeg with `-protocol_whitelist file` and refuses `.m3u8`, `.m3u`, `.ffconcat` and `.txt` inputs; the skill tells Claude to treat ffmpeg messages as untrusted output.
- L5 every failure (OSError, MemoryError, any exception, a broken pipe) is one line and exit 1, never a traceback or exit 120; `need_module` also catches broken binary wheels.
- L6 `key_decontam` never overwrites its own input, even with `--force`.
- L8 16-bit PNGs are judged on the 8-bit scale in `--check`; `--lo` must be 0..255.
- Network (UNC) paths are refused for every input and output.

Known limits (new, documented in the README)
- L7 `--timeout` can overrun when `ffmpeg` on PATH is a wrapper script that starts a child process (1 trial: 26 s instead of 3 s). The real ffmpeg binary is not affected.
- `blank_beats --size 9999x9999` is accepted (about 3.6 GB per frame); the duck loop is plain Python (slow on very long files at 240 fps); UNC refusal is by name only.

Behaviour changes to be aware of: images above 25 megapixels are now refused (were 60); a typo on the command line exits 1 (was 2).

## [0.1.0] (2026-11-19)
First version: three media tools and a dependency check, generalised from private working scripts.

- `key-decontam` (`scripts/key_decontam.py`): green-screen keying for flat outlined art plus the `--check` fringe gate. The keying maths is unchanged; on a synthetic sheet the output is byte-identical to the original script (1 trial).
- `duck-curve` (`scripts/duck_curve.py`): per-frame music gain curve from a speech WAV. Frame rate, duck depth, threshold, hold, look-ahead, attack and release are options. With `--fps 25` and `--fps 60` and default options it matches the two earlier variants of the same algorithm exactly (max difference 0.0 on 44.1 kHz and 48 kHz test files, 1 trial each). It no longer needs scipy: a small built-in reader handles PCM 8/16/24/32 and float 32/64 WAV.
- `blank-beats` (`scripts/blank_beats.py`): near-empty seconds of a video. The background colour is now the `--bg` option (default `#FFF8EF`); tolerance, coverage, step, size, `--min-run`, `--format json`, `--fail-above` and `--timeout` are options. Output text is the same as the original on a test video.
- `keyframe-doctor` (`scripts/kk_doctor.py`): reports Python, numpy, OpenCV and ffmpeg per tool with the install command.

Behaviour that differs from the private originals, on purpose (each has a regression test):
- `blank_beats`: an ffmpeg failure (unreadable file, audio-only file, timeout) is now an error with exit code 1; the original printed "near-empty seconds: 0.0" and exited 0. Frames are streamed instead of loaded whole.
- `duck_curve`: float and 8/24/32-bit WAVs are scaled correctly (the original divided by 32768 whatever the type, so a float WAV looked silent); unsupported encodings are refused. Frame boundaries use exact `sample rate / fps` instead of an integer hop, so non-integer ratios no longer drift.
- `key_decontam`: no crash when there is no foreground or no object (`NameError` / index error in the original); `--check` no longer exits 0 when files are unreadable, have no alpha channel or too few edge pixels: they are reported as `SKIP` or `ERR`, and a run that judged nothing exits 1. Exit codes follow one scheme in every tool: 0 ok, 2 finding, 1 tool error.
- All tools: missing dependencies give one clear line and exit 1; `--help` works with nothing installed; inputs must be existing regular files; outputs are never overwritten without `--force`, `duck_curve` writes only `.json`, `key_decontam` only its fixed names.

Known limits (see README): flat art only; absolute pixel thresholds; WAV only; colour-only blankness; one background colour per run.

Not verified: Python 3.8 to 3.13, Linux, macOS, CI (the workflow is untested), real-world sheets, loudness quality of the curve by ear. Verified: 40 unit and CLI tests on Windows with Python 3.14, numpy 2.5.4, opencv-python-headless 5.1.0.95, ffmpeg 9.0.2 (1 run). One headless live run (`claude -p --permission-mode bypassPermissions --plugin-dir`, Sonnet): the duck-curve skill ran its script and wrote the JSON (1 trial). Not verified: that `allowed-tools` avoids every permission prompt without bypass (a headless default-mode attempt with Haiku never loaded the skill, so it proved nothing), and the other three skills live.
