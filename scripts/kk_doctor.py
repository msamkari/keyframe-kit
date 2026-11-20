# -*- coding: utf-8 -*-
"""kk_doctor.py - check what keyframe-kit needs, tool by tool, and say how to fix what is missing.

Usage
  kk_doctor.py [--tool key_decontam|duck_curve|blank_beats]

Read-only: imports numpy and cv2 and runs `ffmpeg -version`. Exit codes: 0 everything the chosen tools
need is present, 1 something is missing.
"""
import argparse
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

NEEDS = {
    "key_decontam": ("numpy", "cv2"),
    "duck_curve": ("numpy",),
    "blank_beats": ("numpy", "ffmpeg"),
}
LABEL = {"numpy": "numpy (BSD-3-Clause)", "cv2": "opencv-python or opencv-python-headless (Apache-2.0)",
         "ffmpeg": "ffmpeg (external program; its licence depends on the build)"}


def module_status(name):
    try:
        mod = importlib.import_module(name)
    except Exception as exc:  # ImportError, or a broken binary wheel
        return False, "%s: %s" % (type(exc).__name__, exc)
    return True, getattr(mod, "__version__", "unknown version")


def ffmpeg_status():
    path = C.which_ffmpeg()
    if not path:
        return False, "not found on PATH"
    problem = C.ffmpeg_problem(path)
    if problem:
        return False, problem
    ver = C.ffmpeg_version(path)
    if not ver:
        return False, "found at %s but `ffmpeg -version` did not run" % path
    return True, ver


def main():
    p = C.KKParser(prog="kk_doctor.py", description=__doc__.split("\n\n")[0])
    p.add_argument("--tool", choices=sorted(NEEDS), action="append", help="check only this tool (repeatable)")
    a = p.parse_args()
    tools = a.tool or sorted(NEEDS)
    print("python %s" % sys.version.split()[0])
    py_ok = sys.version_info >= C.PYTHON_MIN
    if not py_ok:
        print("  FAIL python: %d.%d or newer is required" % C.PYTHON_MIN)
    wanted = []
    for t in tools:
        for dep in NEEDS[t]:
            if dep not in wanted:
                wanted.append(dep)
    status = {}
    for dep in wanted:
        ok, info = ffmpeg_status() if dep == "ffmpeg" else module_status(dep)
        status[dep] = ok
        print("  %s %s: %s" % ("ok  " if ok else "FAIL", LABEL[dep], info))
        if not ok:
            print("       fix: %s" % C.INSTALL_HINT[dep])
    print("")
    all_ok = py_ok
    for t in tools:
        miss = [d for d in NEEDS[t] if not status[d]]
        print("  %-13s %s" % (t, "ready" if not miss else "missing: " + ", ".join(miss)))
        all_ok = all_ok and not miss
    return C.EXIT_OK if all_ok else C.EXIT_ERROR


if __name__ == "__main__":
    C.run_main(main)
