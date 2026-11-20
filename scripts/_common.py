# -*- coding: utf-8 -*-
"""Shared helpers for keyframe-kit: exit codes, dependency checks, safe paths.

Exit codes (the same in every tool): 0 clean, 2 the tool found a problem in the
user's media (green fringe, too many empty seconds), 1 the tool could not do its
job (missing dependency, unreadable input, bad option or bad command line, would
overwrite a file). `--help` exits 0. Usage errors exit 1, never 2, so a gate can
never read a mistyped command as a finding.
"""
import argparse
import importlib
import math
import os
import re
import shutil
import subprocess
import sys
import uuid

# Windows: do not search the current folder for programs (a planted ffmpeg.exe / ffmpeg.bat).
os.environ.setdefault("NoDefaultCurrentDirectoryInExePath", "1")

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


def safe_flush_stdout():
    """Flush stdout; if the reader has gone away (broken pipe; Windows reports it as OSError), stay silent."""
    try:
        sys.stdout.flush()
    except (OSError, ValueError):
        _quiet_stdout()
        return False
    return True


def fail(message, code=EXIT_ERROR):
    try:
        sys.stderr.write("keyframe-kit: %s\n" % message)
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    safe_flush_stdout()
    sys.exit(code)


class KKParser(argparse.ArgumentParser):
    """ArgumentParser whose usage errors exit 1 (argparse's 2 would collide with 'finding')."""

    def error(self, message):
        fail("%s: %s (see --help)" % (self.prog, message))


def finite_float(text):
    """argparse type: a float that is neither nan nor inf."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("%r is not a number" % text)
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError("%r is not a finite number" % text)
    return value


def require_python():
    if sys.version_info < PYTHON_MIN:
        fail("Python %d.%d or newer is required (this is %d.%d)"
             % (PYTHON_MIN + sys.version_info[:2]))


def need_module(name):
    """Import a third-party module or raise ToolError with the install command."""
    try:
        return importlib.import_module(name)
    except Exception as exc:  # ImportError, or a broken binary wheel that fails while loading
        hint = INSTALL_HINT.get(name, "python -m pip install " + name)
        raise ToolError("missing Python module '%s' (%s: %s). Install it with: %s. "
                        "Run keyframe-doctor to check every dependency."
                        % (name, type(exc).__name__, exc, hint))


def _in_cwd(path):
    here = os.path.normcase(os.path.realpath(os.getcwd()))
    return os.path.normcase(os.path.realpath(os.path.dirname(os.path.abspath(path)))) == here


def safe_which_ffmpeg():
    """Path of ffmpeg on PATH, or None. A program found in the current folder is never used."""
    path = shutil.which("ffmpeg")
    if path and _in_cwd(path):
        return None
    return path


def find_ffmpeg():
    path = shutil.which("ffmpeg")
    if path and _in_cwd(path):
        raise ToolError("the ffmpeg found (%s) is in the current folder, which is not trusted. "
                        "Run from another folder, or install a real ffmpeg on PATH. %s."
                        % (path, INSTALL_HINT["ffmpeg"]))
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


_LOCAL_DEVICE_PREFIX = re.compile(r"^\\\\\?\\[A-Za-z]:\\")   # local extended-length path, drive form


def _unc_like(path):
    """True if a path is a network (UNC) or device path once '/' is read as '\' (Windows treats both alike)."""
    norm = path.replace("/", "\\") if os.name == "nt" else path
    if os.name != "nt":
        return path.startswith("//")
    return norm.startswith("\\\\") and not _LOCAL_DEVICE_PREFIX.match(norm)


def reject_network_path(path, what):
    """Refuse network (UNC) paths: the name as given and after os.path.abspath (mixed '/\' slashes included).

    The local extended-length form (double backslash, question mark, backslash, drive letter) is allowed.
    """
    if path.startswith("//") or _unc_like(path) or _unc_like(os.path.abspath(path)):
        raise ToolError("%s is a network (UNC) path, which is refused: copy the file to a local folder first (%s)"
                        % (what, path))


def reject_stream_path(path, what):
    """Windows: refuse an NTFS alternate data stream (any ':' after the drive letter), for example s.wav:x.json."""
    if os.name != "nt":
        return
    rest = os.path.abspath(path)
    if rest.startswith("\\?\\"):
        rest = rest[4:]
    rest = os.path.splitdrive(rest)[1]
    if ":" in rest:
        raise ToolError("%s contains ':' after the drive letter (an NTFS alternate data stream), which is refused: %s"
                        % (what, path))


def reject_unsafe_path(path, what):
    reject_network_path(path, what)
    reject_stream_path(path, what)


def _is_reparse(path):
    """True for a symbolic link, or on Windows a junction or symlink (mount point / symlink reparse tag)."""
    if os.path.islink(path):
        return True
    if os.name == "nt":
        try:
            st = os.lstat(path)
        except OSError:
            return False
        tag = getattr(st, "st_reparse_tag", 0)
        return tag in (0xA0000003, 0xA000000C)   # IO_REPARSE_TAG_MOUNT_POINT, IO_REPARSE_TAG_SYMLINK
    return False


def reject_link_in_folders(folder, what):
    """Refuse a folder, or any folder above it, that is a symbolic link or junction (writes would be redirected).

    On POSIX the first path component is skipped (/tmp, /var and /etc are links on macOS).
    """
    cur = os.path.abspath(folder)
    while True:
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        if os.name != "nt" and os.path.dirname(parent) == parent:
            break
        if _is_reparse(cur):
            raise ToolError("%s is inside a folder that is a symbolic link or junction, which is refused "
                            "(the write could land somewhere else): %s" % (what, cur))
        cur = parent


def existing_file(path, what="input"):
    """Absolute path of an existing regular file, else ToolError.

    Requiring a real file means a media tool can never be handed a URL, a pipe
    or an ffmpeg protocol string. Network (UNC) paths are refused.
    """
    if not path or "\x00" in path:
        raise ToolError("%s path is empty or invalid" % what)
    reject_unsafe_path(path, what)
    full = os.path.abspath(path)
    if not os.path.exists(full):
        raise ToolError("%s not found: %s" % (what, path))
    if not os.path.isfile(full):
        raise ToolError("%s is not a regular file: %s" % (what, path))
    return full


def check_output_file(full, force, what="output"):
    """Refuse links and unsafe replacements for one output file path (already absolute).

    Without force the file must not exist at all (a dangling symlink counts as existing).
    With force it must be a plain regular file: not a symbolic link, not a hard link.
    """
    if not os.path.lexists(full):
        return
    if os.path.islink(full):
        raise ToolError("%s is a symbolic link, which is never written through: %s" % (what, full))
    if not force:
        raise ToolError("%s already exists: %s (do not use --force unless the user explicitly asks)" % (what, full))
    if not os.path.isfile(full):
        raise ToolError("%s exists and is not a regular file: %s" % (what, full))
    if os.stat(full).st_nlink > 1:
        raise ToolError("%s has more than one hard link, which is refused even with --force: %s" % (what, full))


def write_output(full, force, mode, writer, recheck=None, what="output"):
    """Write one output file atomically: temp file in the same folder, re-check, then publish.

    writer(fh) fills the open temp file. Without force the file is published with os.link, which fails if the
    name exists, so an existing file is never replaced. With force the target is checked again just before
    os.replace (plain file, no link, no hard link, and recheck() for tool-specific rules). A failure at any
    point removes the temp file and leaves an existing target untouched.
    """
    folder = os.path.dirname(full)
    reject_link_in_folders(folder, what)
    tmp = os.path.join(folder, ".kk-%s.tmp" % uuid.uuid4().hex)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o666)
    try:
        kwargs = {} if "b" in mode else {"encoding": "utf-8"}
        with os.fdopen(fd, "wb" if "b" in mode else "w", **kwargs) as fh:
            writer(fh)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        if force:
            check_output_file(full, True, what)
            if recheck is not None:
                recheck()
            reject_link_in_folders(folder, what)
            os.replace(tmp, full)
        else:
            try:
                os.link(tmp, full)
            except FileExistsError:
                raise ToolError("%s already exists: %s (do not use --force unless the user explicitly asks)"
                                % (what, full))
            except (OSError, NotImplementedError, AttributeError):
                with open(tmp, "rb") as src, open(full, "xb") as dst:   # no hard links here: exclusive create
                    shutil.copyfileobj(src, dst)
            os.unlink(tmp)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def guarded_write_target(path, allowed_exts, force, what="output"):
    """Validate an output file path. Never overwrites unless force and the existing file is a
    plain regular file (no link) with an allowed extension. Network (UNC) paths are refused."""
    if not path or "\x00" in path:
        raise ToolError("%s path is empty or invalid" % what)
    reject_unsafe_path(path, what)
    full = os.path.abspath(path)
    ext = os.path.splitext(full)[1].lower()
    if ext not in allowed_exts:
        raise ToolError("%s must end in %s (got '%s')" % (what, " or ".join(allowed_exts), ext or "no extension"))
    if os.path.isdir(full):
        raise ToolError("%s is a directory: %s" % (what, path))
    check_output_file(full, force, what)
    parent = os.path.dirname(full)
    if not os.path.isdir(parent):
        raise ToolError("folder for %s does not exist: %s" % (what, parent))
    reject_link_in_folders(parent, what)
    return full


def _quiet_stdout():
    """After a broken pipe: point stdout at the null device so the interpreter's final flush stays silent."""
    try:
        fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(fd, sys.stdout.fileno())
        os.close(fd)
    except (OSError, ValueError, AttributeError):
        pass


def run_main(main):
    """Run main(); turn every failure into one line on stderr and exit code 1 (no traceback)."""
    require_python()
    try:
        code = main()
        if not safe_flush_stdout():
            sys.exit(EXIT_ERROR)
    except ToolError as exc:
        fail(str(exc))
    except BrokenPipeError:
        _quiet_stdout()
        sys.exit(EXIT_ERROR)
    except KeyboardInterrupt:
        fail("interrupted")
    except MemoryError:
        fail("out of memory: the input is too large for this machine")
    except OSError as exc:
        fail("file or system error: %s" % " ".join(str(exc).split()))
    except Exception as exc:  # last line of defence: never a traceback
        fail("unexpected error: %s: %s" % (type(exc).__name__, " ".join(str(exc).split())))
    sys.exit(EXIT_OK if code is None else code)
