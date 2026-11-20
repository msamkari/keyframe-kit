---
name: keyframe-doctor
description: Check that the tools keyframe-kit needs are installed (Python version, numpy, OpenCV, ffmpeg) and print the install command for anything missing. Use when a keyframe-kit tool reports a missing module or ffmpeg, or before the first use of key-decontam, duck-curve or blank-beats.
allowed-tools: Bash(python ${CLAUDE_PLUGIN_ROOT}/scripts/kk_doctor.py *)
---

# keyframe-doctor

Run (the script path exactly in this form, no quotes around it):

python ${CLAUDE_PLUGIN_ROOT}/scripts/kk_doctor.py

To check one tool only, add `--tool key_decontam`, `--tool duck_curve` or `--tool blank_beats`.

It is read-only. Output: one line per dependency (`ok` or `FAIL` with the reason and the install command), then one line per tool (`ready` or `missing: ...`). Exit code 0 means everything needed is present, 1 means something is missing.

Report what is missing and the printed fix command. Install nothing yourself: installing is the user's decision. Tell the user what each dependency is: numpy (BSD-3-Clause), opencv-python-headless or opencv-python (Apache-2.0), and ffmpeg, an external program whose licence (LGPL or GPL) depends on the build they install and which this plugin neither bundles nor links.
