# -*- coding: utf-8 -*-
"""blank_beats.py - find the seconds of a video that are almost empty.

Samples the video at a fixed step (default every 0.05 s), shrinks each frame, and counts the share of
pixels that differ from the background colour. A frame whose share is below --coverage is "near-empty".
Prints the total near-empty seconds and the runs (start, end) in seconds.

Usage
  blank_beats.py [options] VIDEO

Options that matter
  --bg '#FFF8EF'     the ground colour of your video (default #FFF8EF, a warm off-white)
  --tolerance 40     colour distance (0-441) above which a pixel counts as content
  --coverage 0.004   share of content pixels below which a frame is empty (faint grid lines stay empty)

Exit codes: 0 ran (and is within --fail-above, if given), 2 near-empty seconds exceed --fail-above,
1 tool error (missing ffmpeg or numpy, unreadable video, ffmpeg failure, no frames decoded).
Needs: numpy and ffmpeg on PATH. Frames are streamed, so long videos do not fill memory.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

DEFAULT_BG = "#FFF8EF"
# demuxers accepted for the input: common video containers only, so a concat / ffconcat / HLS text file
# renamed to .mp4 is refused by ffmpeg itself
FORMAT_WHITELIST = ("mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi,mpegts,flv,mpeg,ogg,asf,gif,apng,"
                    "m4v,h264,hevc,mpegvideo,ivf,nut,mxf,dv")
PLAYLIST_EXTS = (".m3u8", ".m3u", ".ffconcat", ".txt")   # text files ffmpeg would parse and follow


def parse_color(text):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})", (text or "").strip())
    if not m:
        raise C.ToolError("--bg must be a hex colour like #FFF8EF or #FFF (got '%s')" % text)
    h = m.group(1)
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def parse_size(text):
    m = re.fullmatch(r"(\d{1,4})x(\d{1,4})", (text or "").strip())
    if not m or int(m.group(1)) < 2 or int(m.group(2)) < 2:
        raise C.ToolError("--size must look like 108x192 (width x height, at least 2x2)")
    return int(m.group(1)), int(m.group(2))


def kill_tree(proc):
    """Stop the process this script started and everything it started (a wrapper script's children).

    Only the PID of our own child is used, never a name.
    """
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=20)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def coverage_stream(np, ffmpeg, video, step, w, h, bg, tolerance, timeout):
    """Yield the content coverage (0..1) of each sampled frame. Raises ToolError on ffmpeg failure."""
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-protocol_whitelist", "file", "-format_whitelist", FORMAT_WHITELIST, "-i", video, "-an", "-sn",
           "-vf", "fps=%r,scale=%d:%d" % (1.0 / step, w, h),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    if os.name == "nt":
        group = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    else:
        group = {"start_new_session": True}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, **group)
    errbuf = []

    def drain():
        errbuf.append(proc.stderr.read(20000))
        proc.stderr.read()  # discard the rest so ffmpeg never blocks on a full pipe

    t_err = threading.Thread(target=drain, daemon=True)
    t_err.start()
    timed_out = []

    def on_timeout():
        timed_out.append(True)
        kill_tree(proc)

    timer = threading.Timer(timeout, on_timeout)
    timer.start()
    size = w * h * 3
    bgv = np.array(bg, dtype=np.int32)
    tol2 = float(tolerance) ** 2
    frames = 0
    try:
        while True:
            chunk = proc.stdout.read(size)
            if len(chunk) < size:
                break
            fr = np.frombuffer(chunk, np.uint8).reshape(h, w, 3).astype(np.int32)
            dist2 = ((fr - bgv) ** 2).sum(axis=2)
            frames += 1
            yield float((dist2 > tol2).mean())
    finally:
        timer.cancel()
        try:
            proc.stdout.close()
        except OSError:
            pass
        if proc.poll() is None:
            kill_tree(proc)
        proc.wait()
        t_err.join(timeout=5)
    msg = (errbuf[0].decode("utf-8", "replace").strip() if errbuf else "")
    if timed_out:
        raise C.ToolError("ffmpeg ran longer than --timeout %s s and was stopped" % timeout)
    if proc.returncode != 0:
        raise C.ToolError("ffmpeg failed (exit %s): %s" % (proc.returncode, msg[:400] or "no message"))
    if frames == 0:
        raise C.ToolError("no frames were decoded from the video%s" % (": " + msg[:400] if msg else ""))


def find_runs(empty, step):
    runs, i, n = [], 0, len(empty)
    while i < n:
        if empty[i]:
            j = i
            while j < n and empty[j]:
                j += 1
            runs.append((round(i * step, 2), round(j * step, 2)))
            i = j
        else:
            i += 1
    return runs


def build_parser():
    p = C.KKParser(prog="blank_beats.py", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video", metavar="VIDEO")
    p.add_argument("--bg", default=DEFAULT_BG, help="background colour, hex (default %s)" % DEFAULT_BG)
    p.add_argument("--tolerance", type=C.finite_float, default=40.0, help="colour distance counted as content (default 40)")
    p.add_argument("--coverage", type=C.finite_float, default=0.004, help="content share below which a frame is empty (default 0.004)")
    p.add_argument("--step", type=C.finite_float, default=0.05, help="seconds between sampled frames (default 0.05)")
    p.add_argument("--size", default="108x192", help="analysis size WxH, any aspect (default 108x192)")
    p.add_argument("--min-run", type=C.finite_float, default=0.0, help="only report runs at least this many seconds (default 0 = all)")
    p.add_argument("--fail-above", type=C.finite_float, default=None, help="exit 2 when near-empty seconds exceed this")
    p.add_argument("--timeout", type=C.finite_float, default=600.0, help="kill ffmpeg after this many seconds (default 600)")
    p.add_argument("--format", choices=("text", "json"), default="text")
    return p


def main():
    a = build_parser().parse_args()
    bg = parse_color(a.bg)
    w, h = parse_size(a.size)
    if not (0.01 <= a.step <= 5):
        raise C.ToolError("--step must be between 0.01 and 5 seconds")
    if not (0 <= a.tolerance <= 442):
        raise C.ToolError("--tolerance must be between 0 and 441")
    if not (0 <= a.coverage <= 1):
        raise C.ToolError("--coverage must be between 0 and 1")
    if a.min_run < 0 or not (1 <= a.timeout <= 86400):
        raise C.ToolError("--min-run must be >= 0 and --timeout between 1 and 86400")
    np = C.need_module("numpy")
    ffmpeg = C.find_ffmpeg()
    video = C.existing_file(a.video, "video")
    if os.path.splitext(video)[1].lower() in PLAYLIST_EXTS:
        raise C.ToolError("a playlist or list file is not a video (ffmpeg would follow what it lists): %s" % a.video)
    covs = list(coverage_stream(np, ffmpeg, video, a.step, w, h, bg, a.tolerance, a.timeout))
    empty = [c < a.coverage for c in covs]
    runs = [r for r in find_runs(empty, a.step) if r[1] - r[0] >= a.min_run - 1e-9]
    total = round(sum(empty) * a.step, 2)
    if a.format == "json":
        print(json.dumps({"near_empty_seconds": total, "runs": [list(r) for r in runs],
                          "frames": len(covs), "step": a.step, "bg": "#%02X%02X%02X" % bg}))
    else:
        print("near-empty seconds: %s runs: %s" % (total, runs))
    if a.fail_above is not None and total > a.fail_above:
        sys.stderr.write("blank_beats: %s near-empty seconds is above --fail-above %s\n" % (total, a.fail_above))
        return C.EXIT_FINDING
    return C.EXIT_OK


if __name__ == "__main__":
    C.run_main(main)
