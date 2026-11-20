# -*- coding: utf-8 -*-
"""Regression tests for the 0.1.2 red-team fixes (M-1, M-2, L-1..L-5, L-7, shim timeout)."""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from unittest import mock

from test_keyframe_kit import (FFMPEG, Tmp, cv2, load, np, read_text, run, wav_bytes, write_bytes)

WIN = os.name == "nt"
BS = "\\"


def common():
    load("_common.py")
    import _common
    return _common


class TestV012(Tmp):
    def wav(self, name="s.wav", n=4410):
        path = self.p(name)
        write_bytes(path, wav_bytes([0] * n))
        return path

    # -- M-1 -----------------------------------------------------------------------------------
    @unittest.skipUnless(WIN, "mixed-slash UNC forms are a Windows matter")
    def test_m1_mixed_slash_unc_refused_on_the_function(self):
        C = common()
        for bad in ("/" + BS + "host" + BS + "share" + BS + "a.json", BS + "/host/share/a.json",
                    BS + BS + "host" + BS + "s", "//host/s/a", BS + BS + "?" + BS + "UNC" + BS + "h" + BS + "s" + BS + "a"):
            with self.assertRaises(C.ToolError, msg=bad):
                C.reject_network_path(bad, "x")
        C.reject_network_path(BS + BS + "?" + BS + "C:" + BS + "ok" + BS + "a.json", "x")   # local form is fine
        C.reject_network_path("C:/ok/a.json", "x")

    @unittest.skipUnless(WIN and np is not None, "Windows and numpy")
    def test_m1_mixed_slash_unc_refused_on_every_cli(self):
        bads = ["/" + BS + "kk-nohost.invalid" + BS + "share" + BS + "a", BS + "/kk-nohost.invalid/share/a"]
        w = self.wav()
        for bad in bads:
            for args in ([bad + ".wav", self.p("o.json")], [w, bad + ".json"]):
                rc, _, se = run("duck_curve.py", ["--"] + args)
                self.assertEqual(rc, 1, args)
                self.assertIn("network", se)
            rc, _, se = run("blank_beats.py", ["--", bad + ".mp4"])
            self.assertEqual((rc, "network" in se), (1, True))
            rc, so, se = run("key_decontam.py", ["--check", "--", bad + ".png"])
            self.assertEqual((rc, "network" in so + se), (1, True))
            rc, _, se = run("key_decontam.py", ["--", self.p("none.png"), bad])
            self.assertEqual(rc, 1)
            self.assertTrue("network" in se or "not found" in se, se)

    # -- M-2 -----------------------------------------------------------------------------------
    @unittest.skipIf(np is None, "numpy needed")
    def test_m2_sample_cap_rejects_before_converting(self):
        mod = load("duck_curve.py")
        path = self.wav(n=3000)
        with mock.patch.object(mod, "MAX_SAMPLES", 1000, create=True):
            with self.assertRaises(mod.C.ToolError) as cm:
                mod.read_wav(np, path)
        self.assertIn("samples", str(cm.exception))

    @unittest.skipIf(np is None, "numpy needed")
    def test_m2_default_cap_and_chunked_decoding_keep_the_values(self):
        mod = load("duck_curve.py")
        self.assertLessEqual(mod.MAX_SAMPLES, 200 * 1000 * 1000)
        for bits, tag in ((8, 1), (16, 1), (24, 1), (32, 1), (32, 3), (64, 3)):
            n = 40
            if tag == 3:
                samples = [((i * 7) % 20 - 10) / 20.0 for i in range(n)]
            elif bits == 8:
                samples = [(i * 37) % 256 for i in range(n)]
            else:
                top = 2 ** (bits - 1) - 1
                samples = [((i * 7919) % (2 * top)) - top for i in range(n)]
            path = self.p("w%d_%d.wav" % (bits, tag))
            write_bytes(path, wav_bytes(samples, channels=2, bits=bits, tag=tag))
            with mock.patch.object(mod, "CHUNK_FRAMES", 3):
                _, small = mod.read_wav(np, path)
            with mock.patch.object(mod, "CHUNK_FRAMES", 10 ** 9):
                _, big = mod.read_wav(np, path)
            self.assertEqual(small.tolist(), big.tolist(), (bits, tag))
            self.assertEqual(len(big), n // 2)

    # -- L-5 -----------------------------------------------------------------------------------
    @unittest.skipUnless(WIN and np is not None, "NTFS streams are a Windows matter")
    def test_l5_alternate_data_stream_is_refused(self):
        w = self.wav()
        rc, _, se = run("duck_curve.py", ["--", w, w + ":x.json"])
        self.assertEqual(rc, 1)
        self.assertIn("alternate data stream", se)
        rc, _, se = run("duck_curve.py", ["--", w + ":x", self.p("o.json")])
        self.assertEqual(rc, 1)
        self.assertIn("alternate data stream", se)
        self.assertFalse(os.path.exists(self.p("o.json")))
        rc, _, se = run("key_decontam.py", ["--", self.p("a.png"), self.p("out:x")])
        self.assertEqual(rc, 1)
        self.assertTrue("alternate data stream" in se or "not found" in se)
        if cv2 is not None:
            import numpy
            ok, buf = cv2.imencode(".png", numpy.zeros((40, 40, 3), numpy.uint8))
            buf.tofile(self.p("a.png"))
            rc, _, se = run("key_decontam.py", ["--", self.p("a.png"), self.p("out:x")])
            self.assertEqual(rc, 1)
            self.assertIn("alternate data stream", se)

    # -- L-4 -----------------------------------------------------------------------------------
    @unittest.skipIf(FFMPEG is None or np is None, "ffmpeg and numpy needed")
    def test_l4_disguised_concat_is_refused_and_real_containers_still_work(self):
        def make(name, extra):
            path = self.p(name)
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=white:s=64x64:r=5:d=2"]
                           + extra + [path], check=True, stdin=subprocess.DEVNULL, timeout=120)
            return path
        real = make("real.mp4", ["-pix_fmt", "yuv420p"])
        write_bytes(self.p("disguised.mp4"), b"ffconcat version 1.0\nfile 'real.mp4'\n")
        rc, so, se = run("blank_beats.py", ["--", self.p("disguised.mp4")])
        self.assertEqual(rc, 1, (so, se))
        self.assertNotIn("near-empty", so)
        for name, extra in (("a.mov", []), ("a.mkv", []), ("a.avi", [])):
            path = make(name, extra)
            rc, so, se = run("blank_beats.py", ["--", path])
            self.assertIn(rc, (0, 2), (name, so, se))
        self.assertIn(rc, (0, 2))
        rc, _, se = run("blank_beats.py", ["--", real])
        self.assertIn(rc, (0, 2), se)
        self.assertIn("-format_whitelist", read_text(os.path.join(os.path.dirname(__file__), "..", "scripts", "blank_beats.py")))

    # -- L-3 -----------------------------------------------------------------------------------
    def _make_dir_link(self, link, target):
        if WIN:
            done = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
            if done.returncode != 0:
                self.skipTest("mklink /J not available")
            self.addCleanup(lambda: os.path.isdir(link) and os.rmdir(link))
        else:
            try:
                os.symlink(target, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks not permitted")
            self.addCleanup(lambda: os.path.islink(link) and os.unlink(link))

    @unittest.skipIf(np is None, "numpy needed")
    def test_l3_output_through_a_linked_parent_folder_is_refused(self):
        os.mkdir(self.p("elsewhere"))
        self._make_dir_link(self.p("jn"), self.p("elsewhere"))
        rc, _, se = run("duck_curve.py", ["--", self.wav(), self.p("jn", "c.json")])
        self.assertEqual(rc, 1, se)
        self.assertIn("link or junction", se)
        self.assertEqual(os.listdir(self.p("elsewhere")), [])

    @unittest.skipIf(np is None or cv2 is None, "numpy and opencv needed")
    def test_l3_out_dir_that_is_a_link_is_refused(self):
        os.mkdir(self.p("elsewhere"))
        self._make_dir_link(self.p("jn"), self.p("elsewhere"))
        im = np.zeros((120, 120, 3), np.uint8)
        im[:] = (0, 255, 0)
        cv2.rectangle(im, (10, 10), (110, 110), (200, 60, 60), -1)
        cv2.imencode(".png", im)[1].tofile(self.p("sheet.png"))
        rc, _, se = run("key_decontam.py", ["--", self.p("sheet.png"), self.p("jn")])
        self.assertEqual(rc, 1, se)
        self.assertIn("link or junction", se)
        self.assertEqual(os.listdir(self.p("elsewhere")), [])

    # -- L-1 / L-2 -----------------------------------------------------------------------------
    def _run_duck(self, mod, args):
        with mock.patch.object(sys, "argv", ["duck_curve.py"] + args):
            return mod.main()

    @unittest.skipIf(np is None, "numpy needed")
    def test_l1_target_swapped_to_a_hard_link_after_the_check_is_not_written(self):
        mod = load("duck_curve.py")
        w, out, precious = self.wav(), self.p("c.json"), self.p("precious.txt")
        write_bytes(out, b"[0.5]")
        write_bytes(precious, b"keep me")
        real = mod.read_wav

        def swap(npm, path):
            os.remove(out)
            try:
                os.link(precious, out)
            except OSError:
                self.skipTest("hard links not available")
            return real(npm, path)
        with mock.patch.object(mod, "read_wav", swap):
            with self.assertRaises(mod.C.ToolError):
                self._run_duck(mod, ["--force", "--", w, out])
        self.assertEqual(read_text(precious), "keep me")
        self.assertEqual([f for f in os.listdir(self.dir) if f.endswith(".tmp")], [])

    @unittest.skipIf(np is None, "numpy needed")
    def test_l1_target_swapped_to_other_json_after_the_check_is_not_replaced(self):
        mod = load("duck_curve.py")
        w, out = self.wav(), self.p("c.json")
        write_bytes(out, b"[0.5]")
        real = mod.read_wav

        def swap(npm, path):
            write_bytes(out, b'{"settings": 1}')
            return real(npm, path)
        with mock.patch.object(mod, "read_wav", swap):
            with self.assertRaises(mod.C.ToolError):
                self._run_duck(mod, ["--force", "--", w, out])
        self.assertEqual(read_text(out), '{"settings": 1}')

    @unittest.skipIf(np is None, "numpy needed")
    def test_l1_without_force_a_file_appearing_late_is_never_replaced(self):
        mod = load("duck_curve.py")
        w, out = self.wav(), self.p("c.json")
        real = mod.read_wav

        def late(npm, path):
            write_bytes(out, b"someone else")
            return real(npm, path)
        with mock.patch.object(mod, "read_wav", late):
            with self.assertRaises((mod.C.ToolError, OSError)):
                self._run_duck(mod, ["--", w, out])
        self.assertEqual(read_text(out), "someone else")
        self.assertEqual([f for f in os.listdir(self.dir) if f.endswith(".tmp")], [])

    @unittest.skipIf(np is None, "numpy needed")
    def test_l2_failure_in_the_middle_of_the_write_keeps_the_original(self):
        mod = load("duck_curve.py")
        w, out = self.wav(), self.p("c.json")
        write_bytes(out, b"[0.5]")

        def broken(obj, fh, **kw):
            fh.write("[0.1,")
            raise RuntimeError("disk gone")
        with mock.patch.object(mod.json, "dump", broken):
            with self.assertRaises(RuntimeError):
                self._run_duck(mod, ["--force", "--", w, out])
        self.assertEqual(read_text(out), "[0.5]")
        self.assertEqual([f for f in os.listdir(self.dir) if f.endswith(".tmp")], [])

    @unittest.skipIf(np is None, "numpy needed")
    def test_atomic_write_normal_paths(self):
        w, out = self.wav(), self.p("c.json")
        self.assertEqual(run("duck_curve.py", ["--", w, out])[0], 0)
        self.assertEqual(run("duck_curve.py", ["--", w, out])[0], 1)          # exists: refused
        self.assertEqual(run("duck_curve.py", ["--force", "--", w, out])[0], 0)
        self.assertIsInstance(json.loads(read_text(out)), list)
        self.assertEqual([f for f in os.listdir(self.dir) if f.endswith(".tmp")], [])

    # -- L-7 test gap --------------------------------------------------------------------------
    @unittest.skipIf(np is None, "numpy needed")
    def test_l7_folder_as_input_is_refused_by_the_regular_file_check(self):
        rc, _, se = run("duck_curve.py", ["--", self.dir, self.p("o.json")])
        self.assertEqual(rc, 1)
        self.assertIn("not a regular file", se)
        C = common()
        with self.assertRaises(C.ToolError) as cm:
            C.existing_file(self.dir)
        self.assertIn("not a regular file", str(cm.exception))

    # -- shim timeout --------------------------------------------------------------------------
    @unittest.skipIf(np is None, "numpy needed")
    def test_shim_with_a_child_process_stops_at_the_timeout(self):
        shim = self.p("shim")
        os.mkdir(shim)
        if WIN:
            write_bytes(os.path.join(shim, "ffmpeg.cmd"), b"@echo off\r\nping -n 9 127.0.0.1 >nul\r\n")
        else:
            path = os.path.join(shim, "ffmpeg")
            write_bytes(path, b"#!/bin/sh\nsleep 8 &\nwait\n")
            os.chmod(path, 0o755)
        video = self.p("v.mp4")
        write_bytes(video, b"x" * 100)
        env = {"PATH": shim + os.pathsep + os.environ.get("PATH", "")}
        start = time.time()
        rc, _, se = run("blank_beats.py", ["--timeout", "2", "--", video], env=env, cwd=self.dir)
        took = time.time() - start
        self.assertEqual(rc, 1, se)
        self.assertIn("--timeout", se)
        self.assertLess(took, 6.5, "took %.1f s" % took)


if __name__ == "__main__":
    unittest.main()
