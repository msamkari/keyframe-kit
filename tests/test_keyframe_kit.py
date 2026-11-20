# -*- coding: utf-8 -*-
"""Tests for keyframe-kit. Every media file is synthesised here; nothing is read from outside the temp folder."""
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")

try:
    import numpy as np
except ImportError:  # the suite needs numpy to build fixtures; the dependency tests run without it
    np = None
try:
    import cv2
except ImportError:
    cv2 = None

FFMPEG = shutil.which("ffmpeg")


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def write_bytes(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def script(name):
    return os.path.join(SCRIPTS, name)


def run(name, args, env=None, python_flags=("-X", "utf8"), timeout=180, cwd=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    done = subprocess.run([sys.executable] + list(python_flags) + [script(name)] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                          env=full_env, timeout=timeout, cwd=cwd)
    return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")


def load(name):
    sys.path.insert(0, SCRIPTS)
    spec = importlib.util.spec_from_file_location(name[:-3], script(name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def wav_bytes(samples, sr=44100, channels=1, bits=16, tag=1, extensible=False, data_len=None):
    """Build a WAV. `samples` is a flat list of ints (PCM) or floats (tag 3), already interleaved."""
    width = bits // 8
    if tag == 3:
        body = struct.pack("<%d%s" % (len(samples), "f" if bits == 32 else "d"), *samples)
    elif bits == 8:
        body = bytes(samples)
    elif bits == 16:
        body = struct.pack("<%dh" % len(samples), *samples)
    elif bits == 24:
        body = b"".join(struct.pack("<i", s)[:3] for s in samples)
    else:
        body = struct.pack("<%di" % len(samples), *samples)
    if extensible:
        fmt = struct.pack("<HHIIHHHHIH14s", 0xFFFE, channels, sr, sr * channels * width, channels * width, bits,
                          22, bits, 0, tag, b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71")
    else:
        fmt = struct.pack("<HHIIHH", tag, channels, sr, sr * channels * width, channels * width, bits)
    dlen = len(body) if data_len is None else data_len
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", dlen) + body
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def speech_signal(sr=44100, seconds=4.0):
    """Tone burst 1.0-2.5 s at amplitude 0.3, silence elsewhere."""
    t = np.arange(int(sr * seconds)) / sr
    x = np.zeros_like(t)
    m = (t >= 1.0) & (t < 2.5)
    x[m] = 0.3 * np.sin(2 * np.pi * 220 * t[m])
    return x


class Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="kk-test-"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.dir, *parts)


@unittest.skipIf(np is None, "numpy not installed")
class TestDuckCurve(Tmp):
    def curve(self, x, sr=44100, extra=(), name="s.wav", data=None):
        wav = self.p(name)
        with open(wav, "wb") as fh:
            fh.write(data if data is not None else wav_bytes((np.clip(x, -1, 1) * 32767).astype(int).tolist(), sr))
        out = self.p("o.json")
        if os.path.exists(out):
            os.remove(out)
        rc, so, se = run("duck_curve.py", list(extra) + [wav, out])
        return rc, so, se, (json.loads(read_text(out)) if os.path.exists(out) else None)

    def test_ducks_under_speech_and_releases(self):
        rc, so, se, g = self.curve(speech_signal())
        self.assertEqual(rc, 0, se)
        self.assertEqual(len(g), 4 * 25 + 25)
        self.assertAlmostEqual(g[0], 1.0, places=3)
        self.assertAlmostEqual(g[int(2.0 * 25)], 10 ** (-11 / 20), places=2)
        self.assertGreater(g[int(3.9 * 25)], 0.97)
        self.assertTrue(all(0.27 < v <= 1.0001 for v in g))
        self.assertIn("speech fraction", so)

    def test_fps_changes_frame_count(self):
        rc, _, se, g = self.curve(speech_signal(), extra=["--fps", "60"])
        self.assertEqual(rc, 0, se)
        self.assertEqual(len(g), 4 * 60 + 60)

    def test_duck_db_option(self):
        rc, _, se, g = self.curve(speech_signal(), extra=["--duck-db", "-20"])
        self.assertEqual(rc, 0, se)
        self.assertAlmostEqual(min(g), 0.1, places=2)

    def test_short_pause_is_held_long_pause_is_not(self):
        sr = 44100
        t = np.arange(int(sr * 6)) / sr
        x = np.zeros_like(t)
        for a, b in ((0.5, 1.5), (1.6, 2.5), (3.5, 4.5)):  # 0.1 s gap then 1.0 s gap
            m = (t >= a) & (t < b)
            x[m] = 0.3 * np.sin(2 * np.pi * 220 * t[m])
        rc, _, se, g = self.curve(x)
        self.assertEqual(rc, 0, se)
        self.assertLess(g[int(1.55 * 25)], 0.4)   # short gap stays ducked
        self.assertGreater(g[int(3.2 * 25)], 0.9)  # long gap is released

    def test_silence_gives_flat_curve_and_note(self):
        rc, so, se, g = self.curve(np.zeros(44100 * 2))
        self.assertEqual(rc, 0, se)
        self.assertTrue(all(v == 1.0 for v in g))
        self.assertIn("no speech found", so)

    def test_float_and_int_wavs_give_the_same_curve(self):
        """The old script divided by 32768 whatever the dtype, so a float WAV read as silence."""
        x = speech_signal()
        _, _, _, ref = self.curve(x)
        for bits, tag, samples in ((32, 3, x.astype(np.float32).tolist()), (64, 3, x.tolist())):
            rc, _, se, g = self.curve(None, data=wav_bytes(samples, 44100, 1, bits, tag), name="f%d.wav" % bits)
            self.assertEqual(rc, 0, se)
            self.assertLess(max(abs(a - b) for a, b in zip(ref, g)), 0.01, "float%d" % bits)

    def test_all_pcm_widths_and_extensible(self):
        x = speech_signal()
        _, _, _, ref = self.curve(x)
        cases = {
            8: [int(v * 127 + 128) for v in x],
            24: [int(v * 8388607) for v in x],
            32: [int(v * 2147483647) for v in x],
        }
        for bits, samples in cases.items():
            rc, _, se, g = self.curve(None, data=wav_bytes(samples, 44100, 1, bits), name="p%d.wav" % bits)
            self.assertEqual(rc, 0, se)
            self.assertLess(max(abs(a - b) for a, b in zip(ref, g)), 0.01, "pcm%d" % bits)
        s16 = (x * 32767).astype(int).tolist()
        rc, _, se, g = self.curve(None, data=wav_bytes(s16, 44100, 1, 16, 1, extensible=True), name="ext.wav")
        self.assertEqual(rc, 0, se)
        self.assertLess(max(abs(a - b) for a, b in zip(ref, g)), 0.01)

    def test_stereo_is_mixed_down(self):
        x = (speech_signal() * 32767).astype(int)
        inter = np.empty(len(x) * 2, dtype=int)
        inter[0::2], inter[1::2] = x, x
        _, _, _, ref = self.curve(speech_signal())
        rc, _, se, g = self.curve(None, data=wav_bytes(inter.tolist(), 44100, 2), name="st.wav")
        self.assertEqual(rc, 0, se)
        self.assertLess(max(abs(a - b) for a, b in zip(ref, g)), 0.01)

    def test_streamed_header_with_oversized_data_length(self):
        x = (speech_signal() * 32767).astype(int).tolist()
        rc, _, se, g = self.curve(None, data=wav_bytes(x, 44100, 1, 16, data_len=0xFFFFFFFF), name="stream.wav")
        self.assertEqual(rc, 0, se)
        self.assertEqual(len(g), 4 * 25 + 25)

    def test_not_a_wav_is_a_clear_error(self):
        rc, so, se, g = self.curve(None, data=b"ID3\x04" + b"\x00" * 200, name="fake.wav")
        self.assertEqual(rc, 1)
        self.assertIn("not a WAV", se)
        self.assertNotIn("Traceback", se)
        self.assertIsNone(g)

    def test_unsupported_encoding_is_refused_not_misread(self):
        rc, _, se, g = self.curve(None, data=wav_bytes([0, 1, 2, 3], 44100, 1, 16, tag=2), name="adpcm.wav")
        self.assertEqual(rc, 1)
        self.assertIn("unsupported WAV encoding", se)
        self.assertIsNone(g)

    def test_empty_and_truncated(self):
        rc, _, se, _ = self.curve(None, data=wav_bytes([], 44100), name="empty.wav")
        self.assertEqual(rc, 1)
        self.assertIn("no audio", se)
        rc, _, se, _ = self.curve(None, data=b"RIFF\x04\x00\x00\x00WAVE", name="trunc.wav")
        self.assertEqual(rc, 1)
        self.assertNotIn("Traceback", se)

    def test_output_guards(self):
        wav = self.p("s.wav")
        with open(wav, "wb") as fh:
            fh.write(wav_bytes((speech_signal() * 32767).astype(int).tolist()))
        out = self.p("o.json")
        self.assertEqual(run("duck_curve.py", [wav, out])[0], 0)
        rc, _, se = run("duck_curve.py", [wav, out])
        self.assertEqual(rc, 1)
        self.assertIn("already exists", se)
        self.assertEqual(run("duck_curve.py", ["--force", wav, out])[0], 0)
        rc, _, se = run("duck_curve.py", [wav, self.p("o.txt")])
        self.assertEqual(rc, 1)
        self.assertIn(".json", se)
        victim = self.p("keep.txt")
        write_bytes(victim, b"precious")
        self.assertEqual(run("duck_curve.py", ["--force", wav, victim])[0], 1)
        self.assertEqual(read_text(victim), "precious")
        rc, _, se = run("duck_curve.py", [wav, self.p("nodir", "o.json")])
        self.assertEqual(rc, 1)
        self.assertIn("does not exist", se)

    def test_bad_options(self):
        wav = self.p("s.wav")
        with open(wav, "wb") as fh:
            fh.write(wav_bytes([0] * 4410))
        for bad in (["--fps", "0"], ["--fps", "1000"], ["--duck-db", "5"], ["--attack", "0"], ["--threshold-db", "10"]):
            rc, _, se = run("duck_curve.py", bad + [wav, self.p("o.json")])
            self.assertEqual(rc, 1, bad)
            self.assertNotIn("Traceback", se)
        self.assertEqual(run("duck_curve.py", [self.p("missing.wav"), self.p("o.json")])[0], 1)
        self.assertEqual(run("duck_curve.py", [self.dir, self.p("o.json")])[0], 1)

    def test_help_and_no_args(self):
        rc, so, _ = run("duck_curve.py", ["--help"])
        self.assertEqual(rc, 0)
        self.assertIn("--fps", so)
        self.assertEqual(run("duck_curve.py", [])[0], 1)  # usage errors exit 1, never 2 (2 means "finding")


@unittest.skipIf(np is None or cv2 is None, "numpy and opencv needed")
class TestKeyDecontam(Tmp):
    def sheet(self, name="sheet.png", bg=(20, 240, 25)):
        im = np.zeros((400, 700, 3), np.uint8)
        im[:] = bg
        cv2.circle(im, (130, 130), 60, (40, 40, 230), -1)
        cv2.circle(im, (130, 130), 60, (0, 0, 0), 4)
        cv2.rectangle(im, (300, 60), (440, 200), (200, 120, 40), -1)
        cv2.rectangle(im, (300, 60), (440, 200), (0, 0, 0), 4)
        cv2.circle(im, (560, 280), 70, (30, 200, 240), -1)
        cv2.circle(im, (560, 280), 70, (0, 0, 0), 4)
        im = cv2.GaussianBlur(im, (0, 0), 1.2)
        path = self.p(name)
        cv2.imencode(".png", im)[1].tofile(path)
        return path

    def test_keys_a_sheet_and_gate_passes(self):
        out = self.p("out")
        rc, so, se = run("key_decontam.py", [self.sheet(), out])
        self.assertEqual(rc, 0, se)
        self.assertIn("objects 3", so)
        self.assertIn("estimated from the image", so)
        files = sorted(os.listdir(out))
        self.assertEqual(files, ["keyed_sheet.png", "obj_00.png", "obj_01.png", "obj_02.png"])
        rgba = cv2.imdecode(np.fromfile(os.path.join(out, "keyed_sheet.png"), np.uint8), cv2.IMREAD_UNCHANGED)
        self.assertEqual(rgba.shape, (400, 700, 4))
        self.assertEqual(int(rgba[5, 5, 3]), 0)            # background is transparent
        self.assertEqual(int(rgba[130, 130, 3]), 255)      # object interior is opaque
        rc, so, _ = run("key_decontam.py", ["--check"] + [os.path.join(out, f) for f in files[1:]])
        self.assertEqual(rc, 0, so)
        self.assertIn("3 file(s) judged, 0 over", so)

    def test_gate_fails_on_green_fringe(self):
        rgba = np.zeros((300, 300, 4), np.uint8)
        cv2.circle(rgba, (150, 150), 100, (30, 220, 30, 255), -1)  # a green ring that is part of the art edge
        cv2.circle(rgba, (150, 150), 80, (0, 0, 200, 255), -1)
        path = self.p("fringe.png")
        cv2.imencode(".png", rgba)[1].tofile(path)
        rc, so, _ = run("key_decontam.py", ["--check", path])
        self.assertEqual(rc, 2, so)
        self.assertIn("FAIL", so)

    def test_gate_never_passes_silently_on_unjudgeable_files(self):
        noalpha = self.p("rgb.png")
        cv2.imencode(".png", np.full((100, 100, 3), 120, np.uint8))[1].tofile(noalpha)
        rc, so, _ = run("key_decontam.py", ["--check", noalpha])
        self.assertEqual(rc, 1)
        self.assertIn("SKIP", so)
        self.assertIn("not a pass", so)
        corrupt = self.p("bad.png")
        write_bytes(corrupt, b"not an image at all")
        rc, so, _ = run("key_decontam.py", ["--check", corrupt])
        self.assertEqual(rc, 1)
        self.assertIn("ERR", so)
        rc, so, _ = run("key_decontam.py", ["--check", self.p("missing.png")])
        self.assertEqual(rc, 1)
        self.assertNotIn("Traceback", so)

    def test_gate_mixed_files_reports_each(self):
        good = self.p("good.png")
        a = np.zeros((300, 300, 4), np.uint8)
        cv2.circle(a, (150, 150), 100, (0, 0, 200, 255), -1)
        cv2.imencode(".png", a)[1].tofile(good)
        rc, so, _ = run("key_decontam.py", ["--check", good, self.p("nope.png")])
        self.assertEqual(rc, 1)
        self.assertIn("ok", so)
        self.assertIn("1 unreadable", so)

    def test_no_foreground_and_no_object_do_not_crash(self):
        flat = self.p("green.png")
        cv2.imencode(".png", np.full((200, 200, 3), (20, 240, 25), np.uint8))[1].tofile(flat)
        rc, _, se = run("key_decontam.py", [flat, self.p("o1")])
        self.assertEqual(rc, 1)
        self.assertIn("no foreground", se)
        self.assertNotIn("Traceback", se)
        small = self.p("small.png")
        im = np.full((200, 200, 3), (20, 240, 25), np.uint8)
        cv2.circle(im, (100, 100), 30, (40, 40, 230), -1)   # island big enough, object box below min-object
        cv2.imencode(".png", im)[1].tofile(small)
        rc, so, se = run("key_decontam.py", ["--min-object", "20000", small, self.p("o2")])
        self.assertEqual(rc, 0, se)
        self.assertIn("objects 0", so)
        self.assertEqual(os.listdir(self.p("o2")), ["keyed_sheet.png"])

    def test_assumed_background_is_reported(self):
        im = np.full((300, 300, 3), (200, 200, 200), np.uint8)   # grey ground: no green found
        cv2.circle(im, (150, 150), 80, (40, 40, 230), -1)
        path = self.p("grey.png")
        cv2.imencode(".png", im)[1].tofile(path)
        rc, so, se = run("key_decontam.py", [path, self.p("o")])
        self.assertIn("ASSUMED", so)

    def test_overwrite_and_path_guards(self):
        out = self.p("out")
        sheet = self.sheet()
        self.assertEqual(run("key_decontam.py", [sheet, out])[0], 0)
        rc, _, se = run("key_decontam.py", [sheet, out])
        self.assertEqual(rc, 1)
        self.assertIn("already exists", se)
        self.assertEqual(run("key_decontam.py", ["--force", sheet, out])[0], 0)
        self.assertEqual(run("key_decontam.py", [sheet, self.p("a", "b", "c")])[0], 1)   # parent missing
        self.assertFalse(os.path.exists(self.p("a")))
        self.assertEqual(run("key_decontam.py", [sheet])[0], 1)                          # one arg
        self.assertEqual(run("key_decontam.py", [self.p("missing.png"), self.p("x")])[0], 1)
        self.assertEqual(run("key_decontam.py", ["http://example.invalid/a.png", self.p("x")])[0], 1)
        self.assertEqual(run("key_decontam.py", [])[0], 1)

    def test_unicode_paths(self):
        d = self.p("مجلد")
        os.mkdir(d)
        sheet = self.sheet(name=os.path.join("مجلد", "ورقة.png"))
        rc, so, se = run("key_decontam.py", [sheet, os.path.join(d, "ناتج")])
        self.assertEqual(rc, 0, se)


@unittest.skipIf(np is None or FFMPEG is None, "numpy and ffmpeg needed")
class TestBlankBeats(Tmp):
    def video(self, bg="0xFFF8EF", name="v.mp4"):
        path = self.p(name)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=%s:s=216x384:r=25:d=3" % bg,
                        "-vf", "drawbox=x=40:y=100:w=100:h=100:color=red:t=fill:enable='between(t,1,2)'",
                        "-pix_fmt", "yuv420p", path], check=True, stdin=subprocess.DEVNULL, timeout=120)
        return path

    def test_finds_the_empty_runs(self):
        rc, so, se = run("blank_beats.py", [self.video()])
        self.assertEqual(rc, 0, se)
        self.assertIn("near-empty seconds: 1.95", so)
        self.assertIn("(0.0, 1.0)", so)
        self.assertIn("(2.05, 3.0)", so)

    def test_bg_is_a_parameter(self):
        v = self.video(bg="0x203040")
        rc, so, _ = run("blank_beats.py", ["--bg", "#203040", v])
        self.assertEqual(rc, 0)
        self.assertIn("near-empty seconds: 1.95", so)
        rc, so, _ = run("blank_beats.py", [v])  # default ground colour is wrong for this video
        self.assertIn("near-empty seconds: 0.0", so)
        self.assertEqual(run("blank_beats.py", ["--bg", "red", v])[0], 1)
        self.assertEqual(run("blank_beats.py", ["--bg", "#F00", v])[0], 0)

    def test_json_min_run_and_fail_above(self):
        v = self.video()
        rc, so, _ = run("blank_beats.py", ["--format", "json", v])
        data = json.loads(so)
        self.assertEqual(data["near_empty_seconds"], 1.95)
        self.assertEqual(data["bg"], "#FFF8EF")
        self.assertEqual(data["runs"], [[0.0, 1.0], [2.05, 3.0]])
        rc, so, _ = run("blank_beats.py", ["--format", "json", "--min-run", "1.0", v])
        self.assertEqual(json.loads(so)["runs"], [[0.0, 1.0]])
        rc, _, se = run("blank_beats.py", ["--fail-above", "1", v])
        self.assertEqual(rc, 2)
        self.assertEqual(run("blank_beats.py", ["--fail-above", "5", v])[0], 0)

    def test_ffmpeg_failure_is_an_error_not_a_pass(self):
        """The old script printed 'near-empty seconds: 0.0' (exit 0) for a file ffmpeg could not read."""
        bad = self.p("bad.mp4")
        write_bytes(bad, b"garbage")
        rc, so, se = run("blank_beats.py", [bad])
        self.assertEqual(rc, 1)
        self.assertIn("ffmpeg failed", se)
        self.assertNotIn("near-empty", so)
        self.assertNotIn("Traceback", se)

    def test_audio_only_input_is_an_error(self):
        wav = self.p("a.wav")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=1", wav], check=True,
                       stdin=subprocess.DEVNULL, timeout=60)
        rc, so, se = run("blank_beats.py", [wav])
        self.assertEqual(rc, 1)
        self.assertNotIn("near-empty", so)

    def test_input_must_be_a_real_file(self):
        for bad in ("http://example.invalid/x.mp4", "concat:a|b", "pipe:0", self.dir, self.p("missing.mp4")):
            rc, _, se = run("blank_beats.py", [bad])
            self.assertEqual(rc, 1, bad)
            self.assertNotIn("Traceback", se)

    def test_option_validation(self):
        v = self.video()
        for bad in (["--step", "0"], ["--tolerance", "-1"], ["--coverage", "2"], ["--size", "0x0"], ["--size", "abc"],
                    ["--timeout", "0"]):
            rc, _, se = run("blank_beats.py", bad + [v])
            self.assertEqual(rc, 1, bad)
            self.assertNotIn("Traceback", se)

    def test_timeout_kills_ffmpeg(self):
        v = self.video()
        rc, _, se = run("blank_beats.py", ["--timeout", "1", "--step", "0.01", v])
        self.assertIn(rc, (0, 1))   # a fast machine may finish inside 1 s; when it does not, the message must be clear
        if rc == 1:
            self.assertIn("--timeout", se)

    def test_works_on_an_unicode_path(self):
        d = self.p("فيديو")
        os.mkdir(d)
        v = self.video(name=os.path.join("فيديو", "مقطع.mp4"))
        rc, so, se = run("blank_beats.py", [v])
        self.assertEqual(rc, 0, se)


class TestDependenciesAreHidden(Tmp):
    """Run the real CLI with the dependency actually unavailable: clear message, exit 1, no traceback."""

    def test_no_numpy_or_opencv_via_python_S(self):
        # -S drops site-packages, so third-party modules cannot be imported.
        pic, wav, vid = self.p("a.png"), self.p("a.wav"), self.p("a.mp4")
        for f in (pic, wav, vid):
            write_bytes(f, b"x")
        cases = [("key_decontam.py", [pic, self.p("o")], "numpy"),
                 ("duck_curve.py", [wav, self.p("o.json")], "numpy"),
                 ("blank_beats.py", [vid], "numpy")]
        for name, args, mod in cases:
            rc, so, se = run(name, args, python_flags=("-S", "-X", "utf8"))
            self.assertEqual(rc, 1, name)
            self.assertIn("missing Python module '%s'" % mod, se, name)
            self.assertIn("pip install", se)
            self.assertNotIn("Traceback", se + so)
            rc, so, _ = run(name, ["--help"], python_flags=("-S", "-X", "utf8"))
            self.assertEqual(rc, 0, name + " --help must work with nothing installed")

    def test_doctor_reports_missing(self):
        rc, so, se = run("kk_doctor.py", [], python_flags=("-S", "-X", "utf8"))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL numpy", so)
        self.assertIn("pip install", so)
        self.assertIn("missing:", so)
        rc, so, _ = run("kk_doctor.py", ["--tool", "bogus"], python_flags=("-S", "-X", "utf8"))
        self.assertEqual(rc, 1)

    def test_doctor_ok_when_everything_is_there(self):
        if np is None or cv2 is None or FFMPEG is None:
            self.skipTest("this machine lacks a dependency")
        rc, so, _ = run("kk_doctor.py", [])
        self.assertEqual(rc, 0, so)
        self.assertIn("key_decontam  ready", so)

    @unittest.skipIf(np is None, "numpy needed to reach the ffmpeg check")
    def test_no_ffmpeg_on_path(self):
        empty = self.p("emptybin")
        os.mkdir(empty)
        vid = self.p("a.mp4")
        write_bytes(vid, b"x")
        rc, so, se = run("blank_beats.py", [vid], env={"PATH": empty})
        self.assertEqual(rc, 1)
        self.assertIn("ffmpeg was not found", se)
        self.assertIn("install ffmpeg", se)
        self.assertNotIn("Traceback", se)
        rc, so, _ = run("kk_doctor.py", ["--tool", "blank_beats"], env={"PATH": empty})
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", so)



def read_bytes_of(path):
    with open(path, "rb") as fh:
        return fh.read()


def png_bomb(path, w, h):
    """A valid, tiny PNG that declares w x h all-zero 8-bit gray pixels (built row by row, no big buffer)."""
    import zlib

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    comp = zlib.compressobj(9)
    row = b"\x00" * (w + 1)
    data = b"".join(comp.compress(row) for _ in range(h)) + comp.flush()
    write_bytes(path, b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                + chunk(b"IDAT", data) + chunk(b"IEND", b""))


class TestV011(Tmp):
    """Regression tests for the 0.1.1 red-team fixes (M1-M4, L1-L8)."""

    def wav(self, name="s.wav", sr=44100, n=4410, bits=16):
        path = self.p(name)
        write_bytes(path, wav_bytes([0] * n if bits == 16 else [128] * n, sr=sr, bits=bits))
        return path

    # -- M1 ------------------------------------------------------------------------------------
    @unittest.skipIf(np is None or cv2 is None, "numpy and opencv needed")
    def test_m1_pixel_cap_is_enforced_before_decode(self):
        kd = load("key_decontam.py")
        self.assertEqual(os.environ.get("OPENCV_IO_MAX_IMAGE_PIXELS"), str(kd.MAX_PIXELS))
        self.assertLessEqual(kd.MAX_PIXELS, 25 * 1000 * 1000)
        bomb = self.p("bomb.png")
        png_bomb(bomb, 8000, 8000)   # 64 MP, a few hundred KB
        with self.assertRaises(Exception) as cm:
            kd.check_png_header(bomb)   # the header check needs no decode at all
        self.assertIn("megapixels", str(cm.exception))
        rc, so, se = run("key_decontam.py", ["--check", bomb])
        self.assertIn("megapixels", so + se)
        # the OpenCV env limit is a second line of defence for non-PNG formats
        probe = "\n".join([
            "import sys",
            "sys.path.insert(0, %r)" % SCRIPTS,
            "import key_decontam, cv2, numpy as np",
            "raw = np.fromfile(%r, np.uint8)" % bomb,
            "try:",
            "    im = cv2.imdecode(raw, 1)",
            "except Exception:",
            "    im = None",
            "print('NONE' if im is None else 'DECODED')"])
        done = subprocess.run([sys.executable, "-c", probe], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              stdin=subprocess.DEVNULL, timeout=120)
        self.assertIn("NONE", done.stdout.decode())

    # -- M2 ------------------------------------------------------------------------------------
    @unittest.skipIf(np is None, "numpy not installed")
    def test_m2_sample_rate_and_frame_caps(self):
        for sr in (1, 3999, 384001):
            rc, _, se = run("duck_curve.py", [self.wav(sr=sr, bits=8), self.p("o%d.json" % sr)])
            self.assertEqual(rc, 1, sr)
            self.assertIn("sample rate", se)
            self.assertFalse(os.path.exists(self.p("o%d.json" % sr)))
        rc, _, se = run("duck_curve.py", ["--fps", "1", "--tail-sec", "30000", self.wav(), self.p("big.json")])
        self.assertEqual(rc, 1)
        self.assertIn("frames", se)
        self.assertFalse(os.path.exists(self.p("big.json")))
        dc = load("duck_curve.py")
        with self.assertRaises(Exception) as cm:   # refused before any large allocation
            dc.duck_curve(np, np.zeros(10), 4000, fps=240.0, tail_s=60000.0)
        self.assertIn("frames", str(cm.exception))
        self.assertEqual(run("duck_curve.py", [self.wav(sr=4000), self.p("ok.json")])[0], 0)
        self.assertEqual(run("duck_curve.py", [self.wav(sr=384000, n=3840), self.p("ok2.json")])[0], 0)

    # -- M3 ------------------------------------------------------------------------------------
    def test_m3_ffmpeg_planted_in_cwd_is_not_run(self):
        plant = self.p("plant")
        os.mkdir(plant)
        marker = self.p("marker.txt")
        if os.name == "nt":
            write_bytes(os.path.join(plant, "ffmpeg.bat"),
                        ('@echo off\r\necho x> "%s"\r\necho ffmpeg version 6.6.6-planted\r\n' % marker).encode())
        else:
            exe = os.path.join(plant, "ffmpeg")
            write_bytes(exe, ("#!/bin/sh\necho x > '%s'\necho ffmpeg version 6.6.6-planted\n" % marker).encode())
            os.chmod(exe, 0o755)
        empty = self.p("emptybin")
        os.mkdir(empty)
        full_env = dict(os.environ)
        full_env["PATH"] = empty + os.pathsep + os.path.dirname(sys.executable)
        full_env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
        full_env.pop("NoDefaultCurrentDirectoryInExePath", None)
        done = subprocess.run([sys.executable, "-X", "utf8", script("kk_doctor.py"), "--tool", "blank_beats"], cwd=plant,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, env=full_env,
                              timeout=60)
        self.assertNotIn("planted", done.stdout.decode("utf-8", "replace"))
        self.assertFalse(os.path.exists(marker), "planted ffmpeg was executed")

    def test_m3_which_result_in_cwd_is_rejected(self):
        C = load("_common.py")
        plant = self.p("plant2")
        os.mkdir(plant)
        fake = os.path.join(plant, "ffmpeg.exe")
        write_bytes(fake, b"x")
        old_cwd, old_which = os.getcwd(), C.shutil.which
        try:
            os.chdir(plant)
            C.shutil.which = lambda name, *a, **k: fake
            with self.assertRaises(C.ToolError) as cm:
                C.find_ffmpeg()
            self.assertIn("current folder", str(cm.exception))
            self.assertIsNone(C.safe_which_ffmpeg())
        finally:
            C.shutil.which = old_which
            os.chdir(old_cwd)
        self.assertEqual(os.environ.get("NoDefaultCurrentDirectoryInExePath"), "1")

    # -- L1 ------------------------------------------------------------------------------------
    def test_l1_usage_errors_exit_1_and_help_exits_0(self):
        for name, args in (("duck_curve.py", []), ("kk_doctor.py", ["--tool", "nope"]), ("blank_beats.py", ["--bogus"]),
                           ("key_decontam.py", ["--check", "--threshold-pct", "abc", "x.png"])):
            rc, so, se = run(name, args)
            self.assertEqual(rc, 1, name)
            self.assertNotIn("Traceback", se)
        for name in ("duck_curve.py", "kk_doctor.py", "blank_beats.py", "key_decontam.py"):
            self.assertEqual(run(name, ["--help"])[0], 0, name)

    @unittest.skipIf(np is None, "numpy not installed")
    def test_l1_double_dash_handles_names_starting_with_a_dash(self):
        write_bytes(self.p("-s.wav"), wav_bytes([0] * 4410))
        rc, _, se = run("duck_curve.py", ["--", "-s.wav", "-o.json"], cwd=self.dir)
        self.assertEqual(rc, 0, se)
        self.assertTrue(os.path.exists(self.p("-o.json")))

    # -- L2 ------------------------------------------------------------------------------------
    @unittest.skipIf(np is None, "numpy not installed")
    def test_l2_nan_and_inf_options_are_refused(self):
        wav = self.wav()
        for extra in (["--duck-db", "nan"], ["--hold-ms", "nan"], ["--lookahead-ms", "inf"], ["--tail-sec", "nan"],
                      ["--fps", "nan"], ["--attack", "nan"], ["--threshold-db", "nan"]):
            rc, _, se = run("duck_curve.py", extra + [wav, self.p("o.json")])
            self.assertEqual(rc, 1, extra)
            self.assertIn("finite", se)
            self.assertNotIn("Traceback", se)
        self.assertFalse(os.path.exists(self.p("o.json")))
        for extra in (["--fail-above", "nan"], ["--min-run", "nan"], ["--tolerance", "nan"], ["--coverage", "nan"],
                      ["--step", "nan"], ["--timeout", "inf"]):
            rc, _, se = run("blank_beats.py", extra + [wav])
            self.assertEqual(rc, 1, extra)
            self.assertIn("finite", se)
        for extra in (["--threshold-pct", "nan"], ["--lo", "nan"], ["--lo", "inf"]):
            rc, _, se = run("key_decontam.py", extra + ["--check", wav])
            self.assertEqual(rc, 1, extra)
            self.assertIn("finite", se)
        rc, _, se = run("key_decontam.py", ["--lo", "-1", "--check", wav])
        self.assertEqual(rc, 1)
        self.assertIn("--lo", se)

    @unittest.skipIf(np is None, "numpy not installed")
    def test_l2_duck_json_is_written_with_allow_nan_false(self):
        self.assertIn("allow_nan=False", read_text(script("duck_curve.py")))

    # -- L3 / M4 / L6 --------------------------------------------------------------------------
    def can_symlink(self):
        src, dst = self.p("_t_src"), self.p("_t_lnk")
        write_bytes(src, b"x")
        try:
            os.symlink(src, dst)
        except (OSError, NotImplementedError, AttributeError):
            return False
        os.remove(dst)
        return True

    @unittest.skipIf(np is None, "numpy not installed")
    def test_l3_duck_never_writes_through_a_symlink(self):
        if not self.can_symlink():
            self.skipTest("symlinks not available")
        wav = self.wav()
        planted = self.p("planted.bat")
        link = self.p("dang.json")
        os.symlink(planted, link)             # dangling
        self.assertEqual(run("duck_curve.py", [wav, link])[0], 1)
        self.assertFalse(os.path.exists(planted))
        self.assertEqual(run("duck_curve.py", ["--force", wav, link])[0], 1)
        self.assertFalse(os.path.exists(planted))
        target = self.p("notjson.cfg")
        write_bytes(target, b"[1, 2]")
        link2 = self.p("lnk.json")
        os.symlink(target, link2)
        self.assertEqual(run("duck_curve.py", ["--force", wav, link2])[0], 1)
        self.assertEqual(read_text(target), "[1, 2]")

    @unittest.skipIf(np is None or cv2 is None, "numpy and opencv needed")
    def test_l3_key_never_writes_through_a_symlink(self):
        if not self.can_symlink():
            self.skipTest("symlinks not available")
        im = np.full((300, 300, 3), (20, 240, 25), np.uint8)
        cv2.circle(im, (150, 150), 80, (40, 40, 230), -1)
        sheet = self.p("sheet.png")
        cv2.imencode(".png", im)[1].tofile(sheet)
        out = self.p("kd")
        os.mkdir(out)
        evil = self.p("evil_target.txt")
        os.symlink(evil, os.path.join(out, "keyed_sheet.png"))
        for extra in ([], ["--force"]):
            self.assertEqual(run("key_decontam.py", extra + [sheet, out])[0], 1)
        self.assertFalse(os.path.exists(evil))

    @unittest.skipIf(np is None, "numpy not installed")
    def test_l3_hardlink_is_refused_with_force(self):
        wav = self.wav()
        a, b = self.p("a.json"), self.p("b.json")
        write_bytes(a, b"[1.0]")
        try:
            os.link(a, b)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("hard links not available")
        self.assertEqual(run("duck_curve.py", ["--force", wav, b])[0], 1)
        self.assertEqual(read_text(a), "[1.0]")

    @unittest.skipIf(np is None, "numpy not installed")
    def test_m4_force_replaces_only_a_number_array(self):
        wav = self.wav()
        for content in (b'{"keep": 1}', b'{"name": "x", "version": "1.0.0"}', b'["a"]', b'[true]', b'[1, 2', b'hello',
                        b'[NaN]', b'[[1]]'):
            victim = self.p("settings.json")
            write_bytes(victim, content)
            rc, _, se = run("duck_curve.py", ["--force", wav, victim])
            self.assertEqual(rc, 1, content)
            self.assertIn("--force", se)
            self.assertEqual(read_bytes_of(victim), content)
        for content in (b"", b"[]", b"[1, 0.5, 0.28]"):
            ok = self.p("mine.json")
            write_bytes(ok, content)
            rc, _, se = run("duck_curve.py", ["--force", wav, ok])
            self.assertEqual(rc, 0, se)
            self.assertIsInstance(json.loads(read_text(ok)), list)

    @unittest.skipIf(np is None or cv2 is None, "numpy and opencv needed")
    def test_l6_output_dir_equal_to_source_folder_is_refused(self):
        im = np.full((300, 300, 3), (20, 240, 25), np.uint8)
        cv2.circle(im, (150, 150), 80, (40, 40, 230), -1)
        sub = self.p("same")
        os.mkdir(sub)
        sheet = os.path.join(sub, "keyed_sheet.png")
        cv2.imencode(".png", im)[1].tofile(sheet)
        before = read_bytes_of(sheet)
        for extra in ([], ["--force"]):
            rc, _, se = run("key_decontam.py", extra + [sheet, sub])
            self.assertEqual(rc, 1, extra)
            self.assertIn("input", se)
            self.assertEqual(read_bytes_of(sheet), before)

    # -- L5 ------------------------------------------------------------------------------------
    def test_l5_run_main_turns_every_failure_into_one_line(self):
        import contextlib
        import io
        C = load("_common.py")
        for exc, word in ((OSError("disk on fire"), "disk on fire"), (MemoryError(), "memory"), (ValueError("odd"), "odd")):
            def boom(e=exc):
                raise e
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
                C.run_main(boom)
            self.assertEqual(cm.exception.code, 1)
            lines = err.getvalue().strip().splitlines()
            self.assertEqual(len(lines), 1, err.getvalue())
            self.assertIn(word, lines[0])

    def test_l5_broken_pipe_exits_1_silently(self):
        proc = subprocess.Popen([sys.executable, "-X", "utf8", script("kk_doctor.py")], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        proc.stdout.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.stderr.close()
        self.assertEqual(proc.wait(timeout=60), 1)
        self.assertNotIn("Traceback", err)

    def test_l5_need_module_catches_a_broken_module(self):
        C = load("_common.py")
        plant = self.p("mods")
        os.mkdir(plant)
        write_bytes(os.path.join(plant, "kk_broken_mod.py"), b"raise RuntimeError('broken wheel')\n")
        sys.path.insert(0, plant)
        try:
            with self.assertRaises(C.ToolError) as cm:
                C.need_module("kk_broken_mod")
        finally:
            sys.path.remove(plant)
        self.assertIn("kk_broken_mod", str(cm.exception))

    # -- L8 ------------------------------------------------------------------------------------
    @unittest.skipIf(np is None or cv2 is None, "numpy and opencv needed")
    def test_l8_16_bit_png_is_judged_on_the_8_bit_scale(self):
        im = np.zeros((300, 300, 4), np.uint8)
        cv2.circle(im, (150, 150), 80, (120, 130, 120, 255), -1)   # d = 10 on the 8-bit scale: below the gate
        p8, p16 = self.p("a8.png"), self.p("a16.png")
        cv2.imencode(".png", im)[1].tofile(p8)
        cv2.imencode(".png", im.astype(np.uint16) * 257)[1].tofile(p16)
        r8 = run("key_decontam.py", ["--check", p8])
        r16 = run("key_decontam.py", ["--check", p16])
        self.assertEqual(r8[0], 0, r8)
        self.assertEqual(r16[0], 0, r16)

    # -- UNC and L4 ----------------------------------------------------------------------------
    @unittest.skipIf(np is None, "numpy not installed")
    def test_unc_paths_are_refused_before_any_access(self):
        for bad in ("\\\\host\\share\\x.wav", "//host/share/x.wav"):
            rc, _, se = run("duck_curve.py", [bad, self.p("o.json")])
            self.assertEqual(rc, 1)
            self.assertIn("network", se)
            rc, _, se = run("duck_curve.py", [self.wav(), bad.replace(".wav", ".json")])
            self.assertEqual(rc, 1)
            self.assertIn("network", se)

    @unittest.skipIf(np is None or FFMPEG is None, "numpy and ffmpeg needed")
    def test_l4_playlist_extensions_are_refused(self):
        for ext in (".m3u8", ".M3U", ".ffconcat", ".txt"):
            path = self.p("list" + ext)
            write_bytes(path, b"#EXTM3U\nfile:other.txt\n")
            rc, _, se = run("blank_beats.py", [path])
            self.assertEqual(rc, 1, ext)
            self.assertIn("playlist", se)
        self.assertIn("-protocol_whitelist", read_text(script("blank_beats.py")))


class TestHelpersAndManifest(unittest.TestCase):
    def test_parse_color_and_size(self):
        bb = load("blank_beats.py")
        self.assertEqual(bb.parse_color("#FFF8EF"), (255, 248, 239))
        self.assertEqual(bb.parse_color("fff"), (255, 255, 255))
        for bad in ("", "#GGGGGG", "#12345", "red", "#FFF8EF00"):
            with self.assertRaises(Exception):
                bb.parse_color(bad)
        self.assertEqual(bb.parse_size("108x192"), (108, 192))
        with self.assertRaises(Exception):
            bb.parse_size("1x1")

    @unittest.skipIf(np is None, "numpy needed")
    def test_find_runs(self):
        bb = load("blank_beats.py")
        self.assertEqual(bb.find_runs([True, True, False, True], 0.5), [(0.0, 1.0), (1.5, 2.0)])
        self.assertEqual(bb.find_runs([], 0.5), [])
        self.assertEqual(bb.find_runs([False, False], 0.5), [])

    def test_manifest_and_skills_follow_the_rules(self):
        meta = json.loads(read_text(os.path.join(ROOT, ".claude-plugin", "plugin.json")))
        self.assertEqual(meta["name"], "keyframe-kit")
        self.assertEqual(meta["license"], "MIT")
        self.assertTrue(re.fullmatch(r"\d+\.\d+\.\d+", meta["version"]))
        skills = os.path.join(ROOT, "skills")
        names = sorted(os.listdir(skills))
        self.assertEqual(names, ["blank-beats", "duck-curve", "key-decontam", "keyframe-doctor"])
        for n in names:
            text = read_text(os.path.join(skills, n, "SKILL.md"))
            m = re.search(r"^allowed-tools: (.+)$", text, re.M)
            self.assertIsNotNone(m, n)
            allowed = m.group(1).strip()
            mm = re.fullmatch(r"Bash\(python \$\{CLAUDE_PLUGIN_ROOT\}/scripts/([a-z_]+\.py) \*\)", allowed)
            self.assertIsNotNone(mm, "allowed-tools must be exactly the plugin script: " + allowed)
            self.assertTrue(os.path.isfile(os.path.join(SCRIPTS, mm.group(1))), n)
            cmd = "python ${CLAUDE_PLUGIN_ROOT}/scripts/" + mm.group(1)
            self.assertIn("\n" + cmd, text, "command text must be the same unquoted form")
            self.assertNotIn("Edit", allowed)
            if n in ("duck-curve", "key-decontam"):
                self.assertIn("never pass --force unless the user explicitly asks", text, n)
            self.assertNotIn("Write", allowed)
            self.assertTrue(text.startswith("---\nname: " + n + "\n"), n)

    def test_no_personal_data_in_the_tree(self):
        pat = re.compile(r"[A-Za-z]:[\\/]+Users|/home/[a-z]|[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}"
                         r"|(^|[^A-Za-z0-9])(sk-|ghp_|github_pat_|AIza|xox[bp]-|AKIA)[A-Za-z0-9_-]{16,}")
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for f in files:
                if f.endswith(".png"):
                    continue
                text = read_text(os.path.join(base, f))
                self.assertIsNone(pat.search(text), os.path.join(base, f))


if __name__ == "__main__":
    unittest.main()
