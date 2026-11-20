# Changelog

This project follows [Semantic Versioning](https://semver.org/).

## [0.1.3] (2026-11-20)
Maintenance release from the third Opus red-team round of 0.1.2 (no High; 2 Medium and 2 Low fixed). Narrowing only: no change to the plugin name, licence, `allowed-tools`, defaults or caps, and no new dependency. 9 new tests (83 in all); 8 of them fail on 0.1.2 and pass now (the ninth is a control: ordinary loud float audio still works).

Fixed
- R3-1 (Medium) on Windows an ffmpeg that resolves from PATH to a `.bat` or `.cmd` wrapper is refused by `blank_beats` and reported as a failure by `keyframe-doctor` (clear message, exit 1). cmd.exe re-parses the command line of a batch file, so a video name containing `&` could run a command. Use the real `ffmpeg.exe`. Argument handling (`shell=False`) is unchanged. Other systems are not affected.
- R3-2 (Medium) every tool now switches stdout and stderr to `backslashreplace` before it prints, so a file name the console code page cannot encode (Arabic or Chinese with a cp1252 pipe) no longer aborts `key_decontam --check` half way; all files are judged and the summary line is printed (characters that cannot be encoded show as backslash escapes).
- R3-3 (Low) the planted-ffmpeg guard now ignores relative and empty PATH entries, and refuses an ffmpeg that resolves anywhere inside the current folder tree (real paths, links followed), not only the current folder itself. Side effect: if you run a tool from inside the folder that holds your own ffmpeg, it is refused; run from another folder.
- R3-4 (Low) `duck_curve` refuses a WAV with float samples whose size is above 1000 (the normal range is -1..1; clipping-level values such as 2.0 are still accepted) with one clear line and exit 1; the check runs before the channel mix, so no numpy RuntimeWarning (and no absolute script path) reaches stderr.
- Test change: the shim-timeout test now hands its `.cmd` shim straight to `coverage_stream` on Windows, because a `.cmd` ffmpeg on PATH is refused on purpose (the process-tree kill is still covered).

Known limits (not fixed, from the same round)
- R3-5 `duck_curve` chunks by frames, not samples: with very many channels one chunk can be the whole file in float64; the 200-million-sample cap still bounds it (about 3.2 GB in one step). The 0.1.2 sentence "temporaries stay small" holds for mono and stereo only.
- R3-6 `duck_curve` and `key_decontam` check the file size and then read the file; a file that keeps growing between the two steps can be read in full.
- R3-7 the skills pass `${CLAUDE_PLUGIN_ROOT}` unquoted because the pre-approved `allowed-tools` pattern needs that exact form; if the plugin path contains a space (for example a profile folder `First Last`) the shell splits it and the tool cannot start. Not changed, so permissions stay exactly as approved. Workaround: install the plugin where the path has no space.
- R3-8 `duck_curve` (without `--force`) creates a new `.json` anywhere you can write, for example a missing `.claude/settings.local.json`, holding a number array. Always a number array, so no content injection; do not name such a file as the output.

Not tested: macOS, Linux, WSL (POSIX paths read only; the cwd-tree and relative-PATH changes apply there too); the Claude Code permission matcher (no network in this round); a real `ffmpeg` installed as a `.cmd` shim by a package manager (Scoop and WinGet normally use `.exe`).

Behaviour changes: a `.bat`/`.cmd` ffmpeg is refused on Windows; ffmpeg inside the current folder tree is refused; relative PATH entries are ignored; float WAVs above 1000 are refused.

## [0.1.2] (2026-11-19)
Maintenance release from the second Opus red-team round of 0.1.1 (no High; 2 Medium, 5 Low). No change to the plugin name, licence or `allowed-tools`. 15 new tests (74 in all); each fix test fails on 0.1.1 and passes now.

Fixed
- M-1 UNC check: `reject_network_path` also checks the name after `os.path.abspath` and reads `/` as `\`, so `/\host\share` and `\/host/share/a` are refused; the local form `\\?\C:\...` is no longer refused by mistake (L-6). Windows only.
- M-2 `duck_curve`: at most 200 million samples (frames x channels) are accepted, checked before any conversion, and the audio is converted in chunks of 2 million frames (no second copy of the data; curve values are identical). The 0.1.1 note "before any large allocation" was inaccurate: the old caps (500 MB file, 6 hours) did not bound memory (a 100 MB WAV needed 2.4 GB). Partly: the curve step itself still needs several float64 copies of the audio, so about 2 to 5 GB at the cap.
- L-5 NTFS alternate data streams (`:` after the drive letter) are refused for inputs, outputs and OUT_DIR (Windows).
- L-4 `blank_beats` passes `-format_whitelist` with common video containers, so a concat / ffconcat file renamed `.mp4` fails with exit 1. mp4, mov, mkv, webm, avi, ts and gif were checked. Unusual containers are refused.
- L-3 an output folder (or any folder above it) that is a symbolic link or junction is refused for `duck_curve` and `key_decontam` (Windows: junction and symlink reparse tags; POSIX: links below the first path component). Cloud-sync placeholders are allowed.
- L-1/L-2 outputs are written atomically: a unique temp file in the same folder, then, with `--force`, the target is re-checked (plain file, no link, no hard link, still an empty file or a JSON array of numbers) and replaced with `os.replace`; without `--force` the temp file is published with `os.link`, which never replaces an existing file. A failure leaves the original untouched and removes the temp file. Partly: the re-check and the replace are still two steps (a very narrow race remains).
- L-7 tests: the regular-file check in `existing_file` is now covered (mutation-verified); the UNC test covers mixed slashes.
- `blank_beats` timeout (L7, Windows and POSIX): ffmpeg runs in its own process group or session and is stopped as a tree (`taskkill /T /F /PID` on the PID this tool started, or `killpg`); a wrapper script that starts a child no longer outlives `--timeout` (test: shim sleeping 8 s, timeout 2 s).

Known limits
- Linux and macOS were not run; `blank_beats --size` large; the two partial items above; the 200-million-sample cap limits long stereo files (trim or downmix).

Behaviour changes: `duck_curve` refuses WAVs above 200 million samples; `blank_beats` refuses unusual video containers; outputs under a linked folder are refused.

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
