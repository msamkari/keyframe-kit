# -*- coding: utf-8 -*-
"""Shared helpers for keyframe-kit: exit codes, dependency checks, safe paths.

Exit codes (the same in every tool): 0 clean, 2 the tool found a problem in the
user's media (green fringe, too many empty seconds), 1 the tool could not do its
job (missing dependency, unreadable input, bad option, would overwrite a file).
"""
import importlib
import os
import shutil
import subprocess
import sys

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_FINDING = 2

INSTALL_HINT = {
    "numpy": "python -m pip install numpy",
    "cv2": "python -m pip install opencv-python-headless",
    "ffmpeg": ("install ffmpeg and put it on PATH "
               "(Windows: winget install Gyan.FFmpeg; macOS: brew install ffmpeg; "
               "Debian/Ubuntu: sudo apt install ffmpeg)"),
}
PYTHON_MIN = (3, 8)


class ToolError(Exception):
    """A user-facing failure: printed as one line, exit code 1, no traceback."""


def fail(message, code=EXIT_ERROR):
    sys.stderr.write("keyframe-kit: %s\n" % message)
    sys.stderr.flush()
    sys.exit(code)


def require_python():
    if sys.version_info < PYTHON_MIN:
        fail("Python %d.%d or newer is required (this is %d.%d)"
             % (PYTHON_MIN + sys.version_info[:2]))


def need_module(name):
    """Import a third-party module or raise ToolError with the install command."""
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        hint = INSTALL_HINT.get(name, "python -m pip install " + name)
        raise ToolError("missing Python module '%s' (%s). Install it with: %s. "
                        "Run keyframe-doctor to check every dependency."
                        % (name, exc, hint))


def find_ffmpeg():
    path = shutil.which("ffmpeg")
    if not path:
        raise ToolError("ffmpeg was not found on PATH. %s. "
                        "Run keyframe-doctor to check every dependency." % INSTALL_HINT["ffmpeg"])
    return path


def ffmpeg_version(path):
    """First line of `ffmpeg -version`, or None."""
    try:
        done = subprocess.run([path, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              stdin=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    first = done.stdout.decode("utf-8", "replace").splitlines()
    return first[0].strip() if first else None


def existing_file(path, what="input"):
    """Absolute path of an existing regular file, else ToolError.

    Requiring a real file means a media tool can never be handed a URL, a pipe
    or an ffmpeg protocol string.
    """
    if not path or "\x00" in path:
        raise ToolError("%s path is empty or invalid" % what)
    full = os.path.abspath(path)
    if not os.path.exists(full):
        raise ToolError("%s not found: %s" % (what, path))
    if not os.path.isfile(full):
        raise ToolError("%s is not a regular file: %s" % (what, path))
    return full


def guarded_write_target(path, allowed_exts, force, what="output"):
    """Validate an output file path. Never overwrites unless force and the
    existing file is a regular file with an allowed extension."""
    if not path or "\x00" in path:
        raise ToolError("%s path is empty or invalid" % what)
    full = os.path.abspath(path)
    ext = os.path.splitext(full)[1].lower()
    if ext not in allowed_exts:
        raise ToolError("%s must end in %s (got '%s')" % (what, " or ".join(allowed_exts), ext or "no extension"))
    if os.path.isdir(full):
        raise ToolError("%s is a directory: %s" % (what, path))
    if os.path.exists(full):
        if not force:
            raise ToolError("%s already exists: %s (ask the user before using --force)" % (what, path))
        if not os.path.isfile(full):
            raise ToolError("%s exists and is not a regular file: %s" % (what, path))
    parent = os.path.dirname(full)
    if not os.path.isdir(parent):
        raise ToolError("folder for %s does not exist: %s" % (what, parent))
    return full


def run_main(main):
    """Run main(); turn ToolError into a one-line message and exit code 1."""
    require_python()
    try:
        code = main()
    except ToolError as exc:
        fail(str(exc))
    except KeyboardInterrupt:
        fail("interrupted")
    sys.exit(EXIT_OK if code is None else code)
