# -*- coding: utf-8 -*-
"""key_decontam.py - green-screen keying for flat, outlined cartoon assets, plus a fringe gate.

Method
  1. Estimate the real background colour from the strongly green-dominant pixels
     (falls back to pure green when there are too few).
  2. Alpha from "green dominance" d = g - max(r, b), so muted greens (plants, books) survive.
  3. Un-mix edge pixels: f = (p - (1 - a) * bg) / a  (true colour decontamination).
  4. Pixels with a < 0.35 take their colour from the nearest solid pixel, so no green can remain.
  5. Drop small alpha islands, tighten the matte, despill a 4 px band, light feather.

Usage
  key_decontam.py [options] SHEET_IMAGE OUT_DIR     key a sheet -> OUT_DIR/keyed_sheet.png + obj_NN.png
  key_decontam.py --check [options] FILE.png ...    gate: count green-tinted edge pixels

Exit codes: 0 clean / keyed, 2 --check found a file over the threshold, 1 tool error.
Needs: numpy and opencv-python (or opencv-python-headless). See keyframe-doctor.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_PIXELS = 60 * 1000 * 1000
MAX_OBJECTS = 500


def load_image(cv2, np, path, flags):
    full = C.existing_file(path, "image")
    if os.path.getsize(full) > MAX_FILE_BYTES:
        raise C.ToolError("image is larger than %d MB: %s" % (MAX_FILE_BYTES // 1048576, path))
    try:
        raw = np.fromfile(full, np.uint8)
        img = cv2.imdecode(raw, flags)
    except Exception as exc:  # cv2.error and friends
        raise C.ToolError("could not decode image %s (%s)" % (path, exc))
    if img is None:
        raise C.ToolError("could not decode image (unsupported or corrupt): %s" % path)
    if img.shape[0] * img.shape[1] > MAX_PIXELS:
        raise C.ToolError("image has more than %d megapixels: %s" % (MAX_PIXELS // 1000000, path))
    return img


def key(cv2, np, im_bgr, lo=28.0, min_island=1500):
    """Return (rgba uint8 BGRA, bg colour BGR float32, bg_estimated bool)."""
    im = im_bgr.astype(np.float32)
    b, g, r = im[..., 0], im[..., 1], im[..., 2]
    d = g - np.maximum(r, b)
    m = d > 120
    estimated = bool(m.sum() > 1000)
    bg = np.median(im[m], axis=0) if estimated else np.array([0, 255, 0], np.float32)
    d_bg = float(bg[1] - max(bg[0], bg[2]))
    a = 1.0 - np.clip((d - lo) / max(d_bg - lo, 1.0), 0, 1)
    a3 = np.maximum(a, 1e-3)[..., None]
    f = (im - (1 - a)[..., None] * bg) / a3
    f = np.clip(f, 0, 255)
    core = (a > 0.5).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(core, connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = st[1:, cv2.CC_STAT_AREA] >= min_island
    k = keep[lab]
    k = cv2.dilate(k.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    a = a * k
    solid = (a > 0.9).astype(np.uint8)
    if int(solid.sum()) == 0:
        raise C.ToolError("no foreground found: the image has no solid (non-green) region of at least %d pixels. "
                          "Is the background green? Lower --min-island or --lo for small art." % min_island)
    dist, labels = cv2.distanceTransformWithLabels(1 - solid, cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_PIXEL)
    ys, xs = np.nonzero(solid)
    idx = np.zeros(labels.max() + 1, np.int64)
    idx[labels[ys, xs]] = np.arange(len(ys))
    sy, sx = ys[idx[labels]], xs[idx[labels]]
    near = f[sy, sx]
    use = (a < 0.35)[..., None]
    f = np.where(use, near, f)
    a = np.clip((a - 0.18) / 0.82, 0, 1).astype(np.float32)
    a = cv2.GaussianBlur(a, (0, 0), 0.6)
    band = cv2.dilate((a < 0.5).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    fb, fg_, fr = f[..., 0], f[..., 1], f[..., 2]
    fg_ = np.where(band, np.minimum(fg_, (fr + fb) / 2.0), fg_)
    f = np.dstack([fb, fg_, fr])
    out = np.dstack([f[..., 0], f[..., 1], f[..., 2], a * 255]).clip(0, 255).astype(np.uint8)
    return out, bg, estimated


def objects(cv2, np, rgba, min_area=6000, row_height=200):
    a = (rgba[..., 3] > 40).astype(np.uint8)
    a = cv2.dilate(a, np.ones((25, 25), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(a)
    res = []
    for i in range(1, n):
        x, y, w, h, _area = st[i]
        if w * h >= min_area:
            res.append((int(x), int(y), int(w), int(h)))
    return sorted(res, key=lambda t: (t[1] // row_height, t[0]))


def edge_green(cv2, np, path):
    """(ring pixels, green-tinted ring pixels), or a string naming why the file cannot be judged."""
    x = load_image(cv2, np, path, cv2.IMREAD_UNCHANGED)
    if x.ndim < 3 or x.shape[2] != 4:
        return "no alpha channel"
    a = x[..., 3]
    ring = (a > 10) & (cv2.dilate((a < 10).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0)
    d = x[..., 1].astype(int) - np.maximum(x[..., 0], x[..., 2]).astype(int)
    return int(ring.sum()), int(((d > 15) & ring).sum())


def do_check(cv2, np, args):
    bad = skipped = judged = errors = 0
    for p in args.paths:
        try:
            r = edge_green(cv2, np, p)
        except C.ToolError as exc:
            print("ERR  %s  (%s)" % (p, exc))
            errors += 1
            continue
        if isinstance(r, str) or r[0] < args.min_ring:
            why = r if isinstance(r, str) else "only %d edge pixels (need %d)" % (r[0], args.min_ring)
            print("SKIP  %s  (%s)" % (p, why))
            skipped += 1
            continue
        pct = r[1] / r[0] * 100.0
        judged += 1
        verdict = "FAIL" if pct > args.threshold_pct else "ok  "
        print("%s %5.2f%%  %s" % (verdict, pct, p))
        bad += pct > args.threshold_pct
    print("key_decontam --check: %d file(s) judged, %d over %.2f%%, %d skipped, %d unreadable"
          % (judged, bad, args.threshold_pct, skipped, errors))
    if errors or (skipped and not judged):
        print("not a pass: %s" % ("some files could not be read" if errors else "every file was skipped"))
        return C.EXIT_FINDING if bad else C.EXIT_ERROR
    return C.EXIT_FINDING if bad else C.EXIT_OK


def do_key(cv2, np, args):
    if len(args.paths) != 2:
        raise C.ToolError("keying needs exactly two arguments: SHEET_IMAGE OUT_DIR (or use --check FILE ...)")
    src, outd = args.paths
    im = load_image(cv2, np, src, cv2.IMREAD_COLOR)
    rgba, bg, estimated = key(cv2, np, im, lo=args.lo, min_island=args.min_island)
    objs = objects(cv2, np, rgba, min_area=args.min_object, row_height=args.row_height)
    if len(objs) > MAX_OBJECTS:
        raise C.ToolError("%d objects found (limit %d): raise --min-object" % (len(objs), MAX_OBJECTS))
    outd = os.path.abspath(outd)
    if os.path.exists(outd) and not os.path.isdir(outd):
        raise C.ToolError("OUT_DIR exists and is not a folder: %s" % args.paths[1])
    if not os.path.isdir(outd):
        if not os.path.isdir(os.path.dirname(outd)):
            raise C.ToolError("parent folder of OUT_DIR does not exist: %s" % os.path.dirname(outd))
    names = ["keyed_sheet.png"] + ["obj_%02d.png" % i for i in range(len(objs))]
    targets = [os.path.join(outd, n) for n in names]
    for t in targets:  # validate every target before writing any
        if os.path.exists(t):
            if not args.force:
                raise C.ToolError("output already exists: %s (ask the user before using --force)" % t)
            if not os.path.isfile(t):
                raise C.ToolError("output exists and is not a regular file: %s" % t)
    os.makedirs(outd, exist_ok=True)
    print("bg colour BGR %s (%s)" % ([int(v) for v in bg], "estimated from the image" if estimated
                                     else "ASSUMED pure green: no strong green background found"))
    ok, buf = cv2.imencode(".png", rgba)
    if not ok:
        raise C.ToolError("could not encode keyed_sheet.png")
    buf.tofile(targets[0])
    for t, (x, y, w, h) in zip(targets[1:], objs):
        ok, buf = cv2.imencode(".png", rgba[y:y + h, x:x + w])
        if not ok:
            raise C.ToolError("could not encode %s" % t)
        buf.tofile(t)
    print("objects %d" % len(objs))
    if not objs:
        print("note: no object reached --min-object %d px (bounding box); only keyed_sheet.png was written"
              % args.min_object)
    return C.EXIT_OK


def build_parser():
    p = argparse.ArgumentParser(prog="key_decontam.py", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="*", help="SHEET_IMAGE OUT_DIR, or with --check: PNG files")
    p.add_argument("--check", action="store_true", help="gate mode: count green-tinted edge pixels in keyed PNGs")
    p.add_argument("--force", action="store_true", help="overwrite existing output files (ask the user first)")
    p.add_argument("--lo", type=float, default=28.0, help="green-dominance below which a pixel is fully kept (default 28)")
    p.add_argument("--min-island", type=int, default=1500, help="drop opaque islands smaller than this many pixels (default 1500)")
    p.add_argument("--min-object", type=int, default=6000, help="smallest object bounding box area to crop (default 6000)")
    p.add_argument("--row-height", type=int, default=200, help="rows of objects are grouped by this many pixels for ordering (default 200)")
    p.add_argument("--threshold-pct", type=float, default=0.5, help="--check fails above this percent of green edge pixels (default 0.5)")
    p.add_argument("--min-ring", type=int, default=200, help="--check skips files with fewer edge pixels (default 200)")
    return p


def main():
    args = build_parser().parse_args()
    if not args.paths:
        raise C.ToolError("no input given (see --help)")
    for name in ("min_island", "min_object", "row_height", "min_ring"):
        if getattr(args, name) < 1:
            raise C.ToolError("--%s must be at least 1" % name.replace("_", "-"))
    if not (0 <= args.threshold_pct <= 100):
        raise C.ToolError("--threshold-pct must be between 0 and 100")
    np = C.need_module("numpy")
    cv2 = C.need_module("cv2")
    return do_check(cv2, np, args) if args.check else do_key(cv2, np, args)


if __name__ == "__main__":
    C.run_main(main)
