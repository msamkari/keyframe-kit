# -*- coding: utf-8 -*-
"""duck_curve.py - per-frame gain curve that lowers a music bed under speech.

Reads a speech WAV, finds frames louder than a threshold, fills short pauses, and writes a JSON list
with one gain per video frame: 1.0 in pauses, DUCK_DB (default -11 dB, about 0.28) under speech,
with a fast attack (0.08 s) and a slow release (0.35 s) and a short look-ahead so the bed is already
down when the first word starts.

Usage
  duck_curve.py [options] SPEECH.wav OUT.json

Reads PCM 8/16/24/32-bit and IEEE float 32/64-bit WAV (mono or multi-channel, mixed to mono).
Needs: numpy only. Exit codes: 0 ok, 1 tool error (unreadable or unsupported file, bad option or command line; --help exits 0).
Typical use: multiply the music track by the curve value of the current video frame (Remotion,
ffmpeg volume expressions, a custom renderer). Frame rate must match your video (25 and 60 are common).
"""
import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

MAX_WAV_BYTES = 500 * 1024 * 1024
MIN_RATE, MAX_RATE = 4000, 384000
MAX_SECONDS = 6 * 3600          # longest audio plus tail that is accepted
MAX_EXISTING_BYTES = 256 * 1024 * 1024


def read_wav(np, path):
    """Return (sample_rate, mono float64 array scaled to about -1..1)."""
    full = C.existing_file(path, "speech file")
    size = os.path.getsize(full)
    if size > MAX_WAV_BYTES:
        raise C.ToolError("WAV is larger than %d MB: trim or downmix it first" % (MAX_WAV_BYTES // 1048576))
    with open(full, "rb") as fh:
        blob = fh.read()
    if len(blob) < 12 or blob[:4] not in (b"RIFF", b"RF64") or blob[8:12] != b"WAVE":
        raise C.ToolError("not a WAV file (no RIFF/WAVE header): %s. Convert it first, "
                          "for example: ffmpeg -i in.mp3 out.wav" % path)
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(blob):
        cid = blob[pos:pos + 4]
        clen = struct.unpack("<I", blob[pos + 4:pos + 8])[0]
        body = pos + 8
        if cid == b"fmt " and fmt is None:
            if clen < 16 or body + 16 > len(blob):
                raise C.ToolError("WAV fmt chunk is truncated")
            tag, ch, sr, _br, _ba, bits = struct.unpack("<HHIIHH", blob[body:body + 16])
            if tag == 0xFFFE and clen >= 26 and body + 26 <= len(blob):
                tag = struct.unpack("<H", blob[body + 24:body + 26])[0]
            fmt = (tag, ch, sr, bits)
        elif cid == b"data" and data is None:
            end = min(body + clen, len(blob))  # streamed files may declare 0xFFFFFFFF
            data = blob[body:end]
            if fmt is not None:
                break
        pos = body + clen + (clen & 1)
    if fmt is None:
        raise C.ToolError("WAV has no fmt chunk")
    if data is None:
        raise C.ToolError("WAV has no data chunk")
    tag, ch, sr, bits = fmt
    if ch < 1 or sr < 1:
        raise C.ToolError("WAV header is invalid (channels=%d, rate=%d)" % (ch, sr))
    if not (MIN_RATE <= sr <= MAX_RATE):
        raise C.ToolError("WAV sample rate %d Hz is outside %d..%d Hz: resample it first, "
                          "for example: ffmpeg -i in.wav -ar 48000 out.wav" % (sr, MIN_RATE, MAX_RATE))
    width = bits // 8
    if tag == 1 and bits in (8, 16, 24, 32):
        pass
    elif tag == 3 and bits in (32, 64):
        pass
    else:
        raise C.ToolError("unsupported WAV encoding (format tag %d, %d bits). Supported: PCM 8/16/24/32 and "
                          "float 32/64. Convert with: ffmpeg -i in.wav -c:a pcm_s16le out.wav" % (tag, bits))
    frame_bytes = width * ch
    usable = (len(data) // frame_bytes) * frame_bytes
    data = data[:usable]
    if usable == 0:
        raise C.ToolError("WAV contains no audio samples")
    if usable // frame_bytes / float(sr) > MAX_SECONDS:
        raise C.ToolError("WAV is longer than %d hours: cut it first" % (MAX_SECONDS // 3600))
    if tag == 3:
        x = np.frombuffer(data, dtype="<f4" if bits == 32 else "<f8").astype(np.float64)
    elif bits == 8:
        x = (np.frombuffer(data, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif bits == 16:
        x = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
    elif bits == 24:
        b = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        x = v.astype(np.float64) / 8388608.0
    else:
        x = np.frombuffer(data, dtype="<i4").astype(np.float64) / 2147483648.0
    x = x.reshape(-1, ch).mean(axis=1) if ch > 1 else x
    if not np.all(np.isfinite(x)):
        raise C.ToolError("WAV contains NaN or infinite samples")
    return int(sr), x


def duck_curve(np, x, sr, fps=25.0, duck_db=-11.0, threshold_db=-42.0, hold_ms=250.0,
               lookahead_ms=70.0, attack_s=0.08, release_s=0.35, tail_s=1.0):
    """Return (gains float array, speech bool array)."""
    n = int(np.ceil(len(x) * fps / sr)) + int(round(tail_s * fps))
    if n > MAX_SECONDS * fps:
        raise C.ToolError("the curve would have %d frames (limit %d = %d hours at %g fps): "
                          "use a shorter file, a smaller --tail-sec or a lower --fps" % (n, MAX_SECONDS * fps, MAX_SECONDS // 3600, fps))
    bounds = np.minimum(np.round(np.arange(n + 1) * (sr / fps)).astype(np.int64), len(x))
    cs = np.concatenate([[0.0], np.cumsum(x * x)])
    cnt = (bounds[1:] - bounds[:-1]).astype(np.float64)
    ms = (cs[bounds[1:]] - cs[bounds[:-1]]) / np.maximum(cnt, 1.0)
    rms = np.sqrt(np.maximum(ms, 0.0) + 1e-12)
    s = (20.0 * np.log10(rms + 1e-9)) > threshold_db
    hold = int(round(hold_ms / 1000.0 * fps))
    i = 0
    while i < n:  # fill pauses shorter than `hold` frames between two speech stretches
        if not s[i]:
            j = i
            while j < n and not s[j]:
                j += 1
            if j - i < hold and i > 0 and j < n:
                s[i:j] = True
            i = j
        else:
            i += 1
    tgt = np.where(s, 10.0 ** (duck_db / 20.0), 1.0)
    look = max(0, int(round(lookahead_ms / 1000.0 * fps)))
    la = np.concatenate([tgt[look:], np.ones(look)])
    g = np.zeros(n)
    cur = 1.0
    a_down = 1.0 - np.exp(-1.0 / (attack_s * fps))
    a_up = 1.0 - np.exp(-1.0 / (release_s * fps))
    for i in range(n):
        a = a_down if la[i] < cur else a_up
        cur += (la[i] - cur) * a
        g[i] = cur
    return g, s


def build_parser():
    p = C.KKParser(prog="duck_curve.py", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("speech", metavar="SPEECH.wav")
    p.add_argument("out", metavar="OUT.json")
    p.add_argument("--fps", type=C.finite_float, default=25.0, help="video frame rate; one gain per frame (default 25)")
    p.add_argument("--duck-db", type=C.finite_float, default=-11.0, help="gain under speech in dB, 0 or negative (default -11)")
    p.add_argument("--threshold-db", type=C.finite_float, default=-42.0, help="frames louder than this dBFS count as speech (default -42)")
    p.add_argument("--hold-ms", type=C.finite_float, default=250.0, help="pauses shorter than this stay ducked (default 250)")
    p.add_argument("--lookahead-ms", type=C.finite_float, default=70.0, help="start ducking this early (default 70)")
    p.add_argument("--attack", type=C.finite_float, default=0.08, help="seconds to duck down (default 0.08)")
    p.add_argument("--release", type=C.finite_float, default=0.35, help="seconds to come back up (default 0.35)")
    p.add_argument("--tail-sec", type=C.finite_float, default=1.0, help="extra seconds of frames after the audio ends (default 1)")
    p.add_argument("--force", action="store_true", help="replace an existing OUT.json, only if it is a JSON array of numbers or empty (never without the user's explicit request)")
    return p


def validate(a):
    if not (1.0 <= a.fps <= 240.0):
        raise C.ToolError("--fps must be between 1 and 240")
    if a.duck_db > 0 or a.duck_db < -80:
        raise C.ToolError("--duck-db must be between -80 and 0")
    if not (-120 <= a.threshold_db <= 0):
        raise C.ToolError("--threshold-db must be between -120 and 0")
    for name in ("hold_ms", "lookahead_ms", "tail_sec"):
        if getattr(a, name) < 0 or getattr(a, name) > 60000:
            raise C.ToolError("--%s is out of range" % name.replace("_", "-"))
    for name in ("attack", "release"):
        if not (0.001 <= getattr(a, name) <= 30):
            raise C.ToolError("--%s must be between 0.001 and 30 seconds" % name)


def check_replaceable(path):
    """--force may only replace a file this tool could have written: empty, or a JSON array of numbers."""
    if not os.path.exists(path):
        return
    size = os.path.getsize(path)
    if size == 0:
        return
    why = "--force only replaces an empty file or a JSON array of numbers (the shape this tool writes); %s is not: %s" % (
        "it" if size <= MAX_EXISTING_BYTES else "this file", path)
    if size > MAX_EXISTING_BYTES:
        raise C.ToolError(why)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.loads(fh.read(), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except (ValueError, UnicodeDecodeError, OSError):
        raise C.ToolError(why)
    ok = isinstance(data, list) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in data)
    if not ok:
        raise C.ToolError(why)


def main():
    a = build_parser().parse_args()
    validate(a)
    out = C.guarded_write_target(a.out, (".json",), a.force, "OUT.json")
    if a.force:
        check_replaceable(out)
    np = C.need_module("numpy")
    sr, x = read_wav(np, a.speech)
    g, s = duck_curve(np, x, sr, fps=a.fps, duck_db=a.duck_db, threshold_db=a.threshold_db, hold_ms=a.hold_ms,
                      lookahead_ms=a.lookahead_ms, attack_s=a.attack, release_s=a.release, tail_s=a.tail_sec)
    with C.open_output(out, a.force, "") as fh:
        json.dump([round(float(v), 4) for v in g], fh, allow_nan=False)
    print("%d frames at %g fps, speech fraction %.3f" % (len(g), a.fps, float(s.mean())))
    if not s.any():
        print("note: no speech found above %g dBFS; the curve is flat 1.0. Check the file or lower --threshold-db."
              % a.threshold_db)
    return C.EXIT_OK


if __name__ == "__main__":
    C.run_main(main)
