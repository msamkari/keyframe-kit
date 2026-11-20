# keyframe-kit — a Claude Code plugin (v0.1.0)

Three small, scriptable media tools for people who make videos and reels:

| Tool (skill) | What it does | Needs |
|---|---|---|
| `key-decontam` | Removes a green-screen background from **flat, outlined cartoon art** (stickers, props, characters on a green sheet), crops each object, and has a **gate** (`--check`) that counts green-tinted pixels left on the edges of keyed PNGs. | numpy, OpenCV |
| `duck-curve` | Reads a speech WAV and writes a JSON list with **one music gain per video frame**: 1.0 in pauses, about -11 dB under speech, fast attack, slow release. | numpy |
| `blank-beats` | Finds the **seconds of a video that are almost empty** (only the background colour on screen) and lists the runs. | numpy, ffmpeg |
| `keyframe-doctor` | Checks the dependencies above and prints the install command for what is missing. | - |

[العربية](README.ar.md)

## Requirements (read this first)

The plugin itself is plain Python scripts, but two tools need third-party software. Nothing is installed for you.

| Dependency | Used by | Licence | Install |
|---|---|---|---|
| Python 3.8+ (written for 3.8 syntax; **tested only on 3.14 on Windows**) | all | PSF | python.org |
| numpy | all three tools | BSD-3-Clause | `python -m pip install numpy` |
| opencv-python-headless (or opencv-python) | `key-decontam` | Apache-2.0 (the wheel also bundles LGPL FFmpeg and other libraries) | `python -m pip install opencv-python-headless` |
| ffmpeg on `PATH` | `blank-beats` | LGPL or GPL, **depending on the build you install** | Windows `winget install Gyan.FFmpeg`, macOS `brew install ffmpeg`, Debian/Ubuntu `sudo apt install ffmpeg` |

ffmpeg is run as a separate program; this plugin does not bundle or link it. If a dependency is missing, each tool stops with one line naming it and the install command (exit code 1, no traceback). Ask Claude to "run keyframe-doctor", or run:

```
python scripts/kk_doctor.py [--tool key_decontam|duck_curve|blank_beats]
```

`duck-curve` needs numpy only (it reads WAV files with its own small reader: PCM 8/16/24/32-bit and float 32/64-bit). The `--help` of every tool works with nothing installed.

## Install

```
/plugin marketplace add msamkari/samkari-plugins
/plugin install keyframe-kit@samkari-plugins
```

Or load it locally: `claude --plugin-dir /path/to/keyframe-kit`. Install the dependencies below first.

## Use

Ask Claude: "key out the green background of this sheet", "make a ducking curve for this voiceover at 30 fps", "where is this video blank?". Or run the scripts yourself:

```
python scripts/key_decontam.py SHEET_IMAGE OUT_DIR           # OUT_DIR/keyed_sheet.png + obj_00.png ...
python scripts/key_decontam.py --check keyed_a.png keyed_b.png
python scripts/duck_curve.py --fps 25 SPEECH.wav OUT.json
python scripts/blank_beats.py --bg '#FFF8EF' VIDEO.mp4
```

### key-decontam
Method: estimate the real background colour from the strongly green pixels; alpha from "green dominance" (g minus the larger of r and b), so muted greens such as plants and books survive; un-mix edge pixels (`f = (p - (1-a)*bg) / a`); pixels with low alpha take their colour from the nearest solid pixel; small islands are dropped; the matte is tightened and a 4 px band around the silhouette is despilled.
- Options: `--lo` (28), `--min-island` (1500 px), `--min-object` (6000 px of bounding box), `--row-height` (200 px, groups objects into rows for numbering), `--threshold-pct` (0.5), `--min-ring` (200), `--force`.
- `--check` prints `ok`, `FAIL`, `SKIP` or `ERR` for each file. Exit codes: `0` all judged files clean, `2` at least one `FAIL`, `1` nothing could be judged (every file skipped) or a file could not be read. A skip is never reported as a pass.
- The first output line says whether the background colour was *estimated* or *ASSUMED* (pure green, when no strong green region exists).

### duck-curve
- `--fps` (25) must match your video. Options: `--duck-db` (-11), `--threshold-db` (-42), `--hold-ms` (250: pauses shorter than this stay ducked), `--lookahead-ms` (70), `--attack` (0.08 s), `--release` (0.35 s), `--tail-sec` (1), `--force`.
- Output: a JSON array, `len = ceil(audio seconds * fps) + tail`. Multiply your music volume by the entry for the current frame.

### blank-beats
- `--bg` is the ground colour of your video (default `#FFF8EF`). `--tolerance` (40, colour distance), `--coverage` (0.004, content share below which a frame is empty), `--step` (0.05 s), `--size` (108x192 analysis size, any aspect), `--min-run`, `--format json`, `--fail-above SECONDS` (exit 2), `--timeout` (600 s).
- Frames are streamed from ffmpeg, so memory use does not grow with the length of the video. An ffmpeg failure is an error (exit 1), never "0 near-empty seconds".

## Safety and limits

- Writes only what you name: `key_decontam.py` writes `keyed_sheet.png` and `obj_NN.png` into the output folder; `duck_curve.py` writes one `.json` file; `blank_beats.py` and `--check` write nothing. **No tool overwrites an existing output** unless you pass `--force`, and even then only a regular file of the same kind (`.json`) or the tool's own fixed names.
- Inputs must be existing regular files, so ffmpeg is never handed a URL or protocol string. Image files up to 200 MB and 25 megapixels (the limit is enforced before decoding, so a small "decompression bomb" PNG cannot exhaust memory), WAV files up to 500 MB, sample rate 4000 to 384000 Hz, at most 6 hours of curve. Network (UNC) paths (starting with two slashes) are refused for every input and output. A playlist or list file (`.m3u8`, `.m3u`, `.ffconcat`, `.txt`) is refused as a video, and ffmpeg runs with `-protocol_whitelist file`.
- **`--force` is narrow.** Never through a symbolic link or a hard link. `duck_curve.py --force` replaces only an empty file or a JSON array of numbers (the shape it writes itself), so it cannot overwrite a settings or config `.json`. `key_decontam.py` never overwrites its own input, even with `--force`. Do not pass `--force` unless you mean it; the skills tell Claude to use it only on your explicit request.
- **Exit codes:** 0 ok, 2 a finding (`--check` fringe, `--fail-above`), 1 every error, including a mistyped command line (the argparse exit 2 is remapped to 1 so a gate never reads a typo as a finding). `--help` exits 0. Failures print one line, never a traceback; a closed pipe exits 1 silently. Options such as `--duck-db` must be finite numbers (`nan` and `inf` are refused).
- **ffmpeg is looked up on PATH only**: a program named `ffmpeg` sitting in the current folder is never used (the tools set `NoDefaultCurrentDirectoryInExePath` and refuse a result inside the current folder).
- The skills pre-approve only the plugin's own script (`Bash(python ${CLAUDE_PLUGIN_ROOT}/scripts/<tool>.py *)`). Media files are treated as data; Claude is told never to follow text found inside them.
- `key-decontam` is built for **flat art on a green ground**. Photos, soft shadows, gradients and glass are outside what it was made for. Pixel thresholds (`--min-island`, `--min-object`, `--row-height`) are absolute, not relative to image size: tune them for very small or very large sheets. Objects that touch or overlap come out as one crop.
- `duck-curve` is a level curve, not a mix: no loudness normalisation, no sidechain over multiple speakers; listen to the result. It reads WAV only (convert with `ffmpeg -i voice.mp3 voice.wav`); compressed WAV (ADPCM, A-law) is refused, not misread.
- `blank-beats` measures colour only: it cannot tell whether a frame with content is meaningful, and a wrong `--bg` makes every frame look full.
- New in 0.1.3: on Windows a `.bat`/`.cmd` ffmpeg is refused (use `ffmpeg.exe`); ffmpeg found inside the current folder tree or through a relative PATH entry is not used; float WAVs with samples above 1000 are refused; non-printable file names no longer stop `--check`. Known limit: a plugin path that contains a space breaks the unquoted command in the skills (install where the path has no space); see CHANGELOG for the rest.
- Known limits in 0.1.2: 16-bit PNGs are judged on the 8-bit scale (`>> 8`). `blank_beats.py --size` up to 9999x9999 is accepted and one frame at that size is about 3.6 GB: keep it small. `duck_curve.py` accepts at most 200 million samples (frames x channels; about 69 minutes of mono 48 kHz audio) and then needs roughly 2 to 5 GB of memory: trim or downmix longer files. Speed: the duck loop is plain Python (a 6-hour file at 240 fps takes minutes). Paths: network (UNC) paths are refused by name only (mixed slashes included; mapped drives are not detected); an NTFS alternate data stream (`:` after the drive letter) is refused on Windows; a symbolic link or junction as the output folder or any folder above it is refused (Windows junctions and symlinks by reparse tag, POSIX links below the first path component). Cloud-sync placeholders are not treated as links. The final check before replacing a file with `--force` and the replacement are two steps, so a hostile local process that swaps the target in that tiny gap can still win; there is no `fsync` of the folder. On Windows the ffmpeg process tree is stopped with `taskkill /T /F /PID` on the PID this tool started; a shim that detaches a grandchild into its own job can survive. `blank_beats.py` only opens common video containers (`-format_whitelist`); an unusual container is refused and needs converting first.
- Not verified: Python 3.8 to 3.13, Linux and macOS (the CI workflow in the repository is written for them; it has not run), live invocation of the skills inside Claude Code beyond one headless run (see CHANGELOG), real-world sheets other than the synthetic fixtures in the tests.

## Tests

```
python -X utf8 -m unittest discover -s tests -v
```
83 tests (40 from 0.1.0, 19 regression tests for 0.1.1, 15 for 0.1.2, 9 for 0.1.3).
The tests build their own images, WAV files and a short video with ffmpeg; those that need numpy, OpenCV or ffmpeg skip when it is missing.

## Licence
MIT, see `LICENSE`.
