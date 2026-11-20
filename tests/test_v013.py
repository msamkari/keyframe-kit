# -*- coding: utf-8 -*-
"""Regression tests for the 0.1.3 fixes (R3-1 batch ffmpeg, R3-2 console encoding, R3-3 relative PATH, R3-4 float WAV)."""
import os
import subprocess
import sys
import unittest

from test_keyframe_kit import (Tmp, cv2, load, np, run, script, wav_bytes, write_bytes)

WIN = os.name == "nt"


def common():
    load("_common.py")
    import _common
    return _common


def plant_ffmpeg(folder, marker, windows_name="ffmpeg.cmd"):
    """A fake ffmpeg that leaves a marker file when it runs."""
    os.makedirs(folder, exist_ok=True)
    if WIN:
        path = os.path.join(folder, windows_name)
        write_bytes(path, ('@echo off\r\necho x> "%s"\r\necho ffmpeg version 6.6.6-planted\r\n' % marker).encode())
    else:
        path = os.path.join(folder, "ffmpeg")
        write_bytes(path, ("#!/bin/sh\necho x > '%s'\necho ffmpeg version 6.6.6-planted\n" % marker).encode())
        os.chmod(path, 0o755)
    return path


def run_env(name, args, path_value, cwd, env_extra=None):
    env = dict(os.environ)
    env["PATH"] = path_value
    env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
    env.pop("NoDefaultCurrentDirectoryInExePath", None)
    if env_extra:
        env.update(env_extra)
    done = subprocess.run([sys.executable, "-X", "utf8", script(name)] + list(args), cwd=cwd, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=90)
    return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")


class TestV013(Tmp):
    # -- R3-1 ----------------------------------------------------------------------------------
    def test_r31_batch_wrapper_helper_is_windows_only_and_by_extension(self):
        C = common()
        for ext in (".bat", ".cmd", ".BAT", ".Cmd"):
            self.assertEqual(C.is_batch_wrapper("C:/x/ffmpeg" + ext), WIN, ext)
        for ext in (".exe", "", ".sh"):
            self.assertFalse(C.is_batch_wrapper("C:/x/ffmpeg" + ext), ext)

    @unittest.skipUnless(WIN, "cmd.exe wrappers exist on Windows only")
    def test_r31_doctor_and_blank_beats_refuse_a_cmd_wrapper_on_path(self):
        marker = self.p("marker.txt")
        for name in ("ffmpeg.cmd", "ffmpeg.bat"):
            wrap = self.p("wrap-" + name[-3:])
            plant_ffmpeg(wrap, marker, name)
            work = self.p("work-" + name[-3:])
            os.mkdir(work)
            path_value = wrap + os.pathsep + os.environ.get("PATH", "")
            rc, so, se = run_env("kk_doctor.py", ["--tool", "blank_beats"], path_value, work)
            self.assertNotEqual(rc, 0, so)
            self.assertIn("wrapper", so)
            self.assertNotIn("planted", so)
            video = self.p("x&mkdir,PWNED&.mp4")
            write_bytes(video, b"x" * 100)
            if np is not None:
                rc, so, se = run_env("blank_beats.py", ["--", video], path_value, work)
                self.assertEqual(rc, 1, se)
                self.assertIn("wrapper", se)
            self.assertFalse(os.path.exists(marker), "the wrapper was executed")
            self.assertFalse(os.path.exists(os.path.join(work, "PWNED")))

    # -- R3-2 ----------------------------------------------------------------------------------
    @unittest.skipIf(cv2 is None or np is None, "numpy and OpenCV needed")
    def test_r32_check_survives_names_the_console_encoding_cannot_print(self):
        rgba = np.zeros((300, 300, 4), np.uint8)
        cv2.circle(rgba, (150, 150), 100, (30, 220, 30, 255), -1)
        cv2.circle(rgba, (150, 150), 80, (0, 0, 200, 255), -1)
        names = ["plain1.png", "\u0645\u0644\u0635\u0642.png", "\u65e5\u672c.png", "plain2.png"]
        paths = []
        for n in names:
            path = self.p(n)
            cv2.imencode(".png", rgba)[1].tofile(path)
            paths.append(path)
        rc, so, se = run("key_decontam.py", ["--check", "--"] + paths, python_flags=(),
                         env={"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"})
        self.assertEqual(rc, 2, se)                     # judged, found fringe: never "unexpected error"
        self.assertNotIn("unexpected error", se)
        self.assertEqual(so.count("FAIL"), 4, so)       # every file was processed
        self.assertIn("4 file(s) judged", so)

    def test_r32_harden_stdout_is_safe_when_reconfigure_is_missing(self):
        C = common()
        old = sys.stdout, sys.stderr
        try:
            sys.stdout = sys.stderr = object()          # no .reconfigure at all
            C.harden_stdout()                           # must not raise
        finally:
            sys.stdout, sys.stderr = old

    # -- R3-3 ----------------------------------------------------------------------------------
    def test_r33_in_cwd_covers_the_whole_tree_not_only_the_folder(self):
        C = common()
        proj = self.p("proj")
        os.makedirs(os.path.join(proj, "a", "b"))
        other = self.p("other")
        os.mkdir(other)
        old = os.getcwd()
        try:
            os.chdir(proj)
            self.assertTrue(C._in_cwd(os.path.join(proj, "ffmpeg")))
            self.assertTrue(C._in_cwd(os.path.join(proj, "a", "b", "ffmpeg")))
            self.assertFalse(C._in_cwd(os.path.join(other, "ffmpeg")))
            self.assertFalse(C._in_cwd(os.path.join(self.dir, "proj2", "ffmpeg")))   # same prefix, other folder
        finally:
            os.chdir(old)

    def test_r33_relative_and_in_tree_path_entries_never_run_a_planted_ffmpeg(self):
        marker = self.p("marker.txt")
        proj = self.p("proj")
        plant_ffmpeg(os.path.join(proj, "bin"), marker, "ffmpeg.bat")
        os.makedirs(os.path.join(proj, "deep", "er"))
        plant_ffmpeg(os.path.join(proj, "deep", "er"), marker, "ffmpeg.bat")
        base = os.path.dirname(sys.executable)
        for entry in ("bin", "./bin", os.path.join(proj, "bin"), os.path.join(proj, "deep", "er")):
            rc, so, se = run_env("kk_doctor.py", ["--tool", "blank_beats"], entry + os.pathsep + base, proj)
            self.assertNotEqual(rc, 0, "%s: %s" % (entry, so))
            self.assertNotIn("planted", so)
            self.assertFalse(os.path.exists(marker), "planted ffmpeg ran via PATH entry %r" % entry)

    def test_r33_which_ignores_relative_entries(self):
        C = common()
        proj = self.p("proj")
        plant_ffmpeg(os.path.join(proj, "bin"), self.p("m.txt"), "ffmpeg.exe" if WIN else "ffmpeg")
        old, old_path = os.getcwd(), os.environ.get("PATH")
        try:
            os.chdir(proj)
            os.environ["PATH"] = "bin" + os.pathsep + "" + os.pathsep + "."
            self.assertIsNone(C.which_ffmpeg())
        finally:
            os.chdir(old)
            os.environ["PATH"] = old_path

    # -- R3-4 ----------------------------------------------------------------------------------
    @unittest.skipIf(np is None, "numpy needed")
    def test_r34_huge_or_nonfinite_float_samples_are_refused_cleanly(self):
        cases = [(64, [0.0, 1e300] * 4410), (64, [1e4] * 8820), (32, [5e3] * 8820), (64, [-2e3] * 8820)]
        for i, (bits, samples) in enumerate(cases):
            wav = self.p("huge%d.wav" % i)
            write_bytes(wav, wav_bytes(samples, bits=bits, tag=3))
            out = self.p("o%d.json" % i)
            rc, so, se = run("duck_curve.py", ["--", wav, out])
            self.assertEqual(rc, 1, (bits, samples[:2], so, se))
            self.assertNotIn("RuntimeWarning", se)
            self.assertNotIn("Traceback", se)
            self.assertEqual(len([ln for ln in se.splitlines() if ln.strip()]), 1, se)
            self.assertFalse(os.path.exists(out))

    @unittest.skipIf(np is None, "numpy needed")
    def test_r34_ordinary_loud_float_audio_still_works(self):
        wav = self.p("loud.wav")
        write_bytes(wav, wav_bytes([0.0, 1.0, -1.0, 2.0] * 2205, bits=64, tag=3))   # clipping-level values are fine
        rc, so, se = run("duck_curve.py", ["--", wav, self.p("ok.json")])
        self.assertEqual(rc, 0, se)


if __name__ == "__main__":
    unittest.main()
