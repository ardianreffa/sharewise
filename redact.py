"""Irreversible redaction that blends with its surroundings.

Blur and pixelation keep a transformed copy of the original pixels, and both can be partly reversed or
recognised. Sharewise never looks at the pixels inside a region when it fills it: those pixels are
zeroed first, and the patch is rebuilt only from the ring of pixels AROUND the region (a smooth
interpolation of the background), plus fresh random grain so it does not look flat. The result blends
in, and it contains nothing of what was there. Inside the region the new fill is used at 100%; the soft
edge is made by blending outward over the background, never by mixing in original pixels from inside.

Regions are fractions of the upright image: (x, y, w, h) in 0..1, origin top-left.
"""

from __future__ import annotations

import secrets
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

PAD = {"face": 0.06, "head": 0.06, "text": 0.06, "plate": 0.06, "manual": 0.0}
OVAL = ("face", "head", "manual")          # shapes drawn as ovals; detected text and plates stay rounded rectangles
RING = {"face": 0.30, "head": 0.30, "manual": 0.30}       # faces: sample only the nearest ring (hair, skin, collar) so the head keeps its shape
NECK = 0.9                                 # faces: also look this many face-heights below, where the neck usually is


def skin_likelihood(rgb: np.ndarray) -> np.ndarray:
    """0..1 per pixel: how skin-like a colour is (YCrCb ranges that cover light to dark skin tones).
    Used ONLY on pixels outside the region, so it never reads the face being hidden."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cr = (r - y) * 0.713 + 128; cb = (b - y) * 0.564 + 128
    core = (cr > 135) & (cr < 175) & (cb > 80) & (cb < 128) & (y > 35) & (y < 245) & (r > g) & (r > b)
    near = (cr > 130) & (cr < 180) & (cb > 75) & (cb < 133) & (y > 25) & (r >= b)
    return core.astype(np.float32) * 0.85 + near.astype(np.float32) * 0.15
WORK = 96                                   # the fill is computed on a small grid: it is smooth by design


def to_pixels(region, size, pad=0.0, angle=0.0):
    """Pixel bounding box of a region (x, y, w, h in 0..1, rotated by `angle` degrees about its centre)."""
    W, H = size; x, y, w, h = region
    cx, cy = (x + w / 2) * W, (y + h / 2) * H
    hw, hh = w * (1 + 2 * pad) * W / 2, h * (1 + 2 * pad) * H / 2
    a = np.radians(angle or 0.0); c, s_ = abs(np.cos(a)), abs(np.sin(a))
    ex, ey = hw * c + hh * s_, hw * s_ + hh * c
    x0, y0 = max(0, int(cx - ex)), max(0, int(cy - ey))
    x1, y1 = min(W, int(np.ceil(cx + ex))), min(H, int(np.ceil(cy + ey)))
    return x0, y0, x1, y1


def shape_of(r: dict) -> str:
    return r.get("shape") or ("rect" if r.get("kind") in ("text", "plate") else "oval")


def _polygon(r: dict, size, pad: float):
    """Outline of the shape in pixel coordinates (rotated rectangle or rotated, face-proportioned oval)."""
    W, H = size; x, y, w, h = r["box"]; kind = r.get("kind", "manual")
    cx, cy = (x + w / 2) * W, (y + h / 2) * H
    hw, hh = w * (1 + 2 * pad) * W / 2, h * (1 + 2 * pad) * H / 2
    a = np.radians(r.get("angle") or 0.0); ca, sa = np.cos(a), np.sin(a)
    if shape_of(r) == "rect":
        pts = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    else:
        oy = 0.0
        if kind in ("face", "head"):                     # slimmer than the box and set a little low: hair stays
            hw, oy, hh = hw * 0.82, hh * 0.04, hh * 0.96
        t = np.linspace(0, 2 * np.pi, 72, endpoint=False)
        pts = list(zip(hw * np.cos(t), oy + hh * np.sin(t)))
    return [(cx + px * ca - py * sa, cy + px * sa + py * ca) for px, py in pts]


def _box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """Separable box blur with an integral image; 3 passes approximate a Gaussian."""
    if r < 1:
        return a
    for _ in range(3):
        for axis in (0, 1):
            pad = [(0, 0)] * a.ndim; pad[axis] = (r + 1, r)
            c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis)
            n = c.shape[axis]
            hi = np.take(c, range(2 * r + 1, n), axis=axis)
            lo = np.take(c, range(0, n - 2 * r - 1), axis=axis)
            a = (hi - lo) / (2 * r + 1)
    return a


def _shape_mask(w, h, kind):
    m = Image.new("L", (w, h), 0); d = ImageDraw.Draw(m)
    if kind in ("face", "head"):
        # a face oval: slimmer than the box and set a little low, so hair and the head's outline stay
        d.ellipse((w * 0.09, h * 0.08, w * 0.91 - 1, h - 1), fill=255)
    elif kind == "manual":
        d.ellipse((0, 0, w - 1, h - 1), fill=255)                 # the user's own area: an oval inside the box they drew
    else:
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=min(w, h) * 0.22, fill=255)
    return m


def _small(a: np.ndarray, size) -> np.ndarray:
    return np.asarray(Image.fromarray(a.astype(np.float32), mode="F").resize(size, Image.BOX))


def redact(img: Image.Image, regions: Iterable[dict], _seed=None) -> Image.Image:
    """Return a new RGB image with every region destroyed. `regions`: [{"box": (x,y,w,h), "kind": ...}].
    `_seed` exists only so tests can compare two runs exactly; normal use draws fresh randomness."""
    out = np.array(img.convert("RGB"), dtype=np.uint8)            # whole photo stays 8-bit; only windows go to float
    H, W = out.shape[:2]
    rng = np.random.default_rng(secrets.randbits(64) if _seed is None else _seed)   # grain never derived from the image
    for r in regions:
        kind = r.get("kind", "manual"); pad = PAD.get(kind, 0.0); shp = shape_of(r)
        x0, y0, x1, y1 = to_pixels(r["box"], (W, H), pad, r.get("angle") or 0.0)
        bw, bh = x1 - x0, y1 - y0
        if bw < 2 or bh < 2:
            continue
        feather = max(3, int(min(bw, bh) * (0.12 if shp == "oval" else 0.09)))
        m = int(max(bw, bh) * RING.get(kind, 0.6)) + 4 + feather * 2   # a ring of surroundings around the region
        below = int(bh * NECK) if (kind in OVAL and shp == "oval") else 0
        wx0, wy0, wx1, wy1 = max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m + below)
        win = out[wy0:wy1, wx0:wx1].astype(np.float32); ww, wh = wx1 - wx0, wy1 - wy0
        shape = Image.new("L", (ww, wh), 0)
        ImageDraw.Draw(shape).polygon([(px - wx0, py - wy0) for px, py in _polygon(r, (W, H), pad)], fill=255)
        inside = np.asarray(shape, dtype=np.float32) / 255.0
        if shp == "rect":
            # signs, plates, house numbers: one flat colour, the most common colour inside the shape (the plate
            # itself), plus fresh grain. Text lives in the strokes, not in the plate colour, so nothing readable is
            # kept: the sign stays a sign, with nothing written on it.
            px_in = win[inside > 0.5]
            base = np.median(px_in, axis=0) if len(px_in) else win.reshape(-1, 3).mean(axis=0)
            big = np.broadcast_to(base, win.shape).astype(np.float32).copy()
            grain = rng.normal(0, 2.0, size=(wh, ww)).astype(np.float32)
            big += grain[..., None]
            soft = max(1, feather // 3)                      # a thin anti-aliased edge, no colour spill
            outer = np.asarray(shape.filter(ImageFilter.GaussianBlur(soft)), dtype=np.float32) / 255.0
            alpha = np.maximum(inside, outer); alpha[inside > 0] = 1.0
            out[wy0:wy1, wx0:wx1] = np.clip(np.clip(big, 0, 255) * alpha[..., None] + win * (1 - alpha[..., None]) + 0.5, 0, 255).astype(np.uint8)
            continue
        # 1) forget the inside: zeroed (with a safety margin) before anything is computed
        guard = np.asarray(shape.filter(ImageFilter.MaxFilter(5)), dtype=np.float32) / 255.0
        known = (guard < 0.5).astype(np.float32)
        if kind in OVAL and shp == "oval":
            # weight skin-coloured pixels OUTSIDE the face (neck, ears, arms) far above hair and background,
            # so the patch reads as skin instead of a grey mask. The inside is already excluded by `known`.
            skin = skin_likelihood(win) * known
            if skin.sum() > 25:
                known = known * (0.06 + 0.94 * skin)
        src = win * known[..., None]
        # 2) rebuild from the ring only: normalised convolution on a small grid
        s = WORK / max(ww, wh); size = (max(2, int(ww * s)), max(2, int(wh * s)))
        num = np.stack([_small(src[..., c], size) for c in range(3)], axis=-1)
        den = _small(known, size)
        fill = np.zeros_like(num); done = np.zeros(den.shape, bool)
        for rad in (max(1, size[0] // 10), max(2, size[0] // 5), max(3, size[0] // 2), max(4, size[0])):
            n_ = np.stack([_box_blur(num[..., c], rad) for c in range(3)], axis=-1); d_ = _box_blur(den, rad)
            ok = (d_ > 1e-3) & ~done
            fill[ok] = n_[ok] / d_[ok][..., None]; done |= ok
        if (~done).any():
            fill[~done] = num.reshape(-1, 3).sum(0) / max(float(den.sum()), 1e-6)
        fill = np.stack([_box_blur(fill[..., c], max(1, size[0] // 8)) for c in range(3)], axis=-1)   # no streaks
        big = np.stack([np.asarray(Image.fromarray(fill[..., c].astype(np.float32), mode="F").resize((ww, wh), Image.BICUBIC))
                        for c in range(3)], axis=-1)
        grain = rng.normal(0, 2.4, size=(wh, ww)).astype(np.float32)
        grain = (grain + np.roll(grain, 1, 0) + np.roll(grain, 1, 1) + np.roll(np.roll(grain, 1, 0), 1, 1)) / 2.0
        big += grain[..., None]                                              # fine, soft grain (fresh randomness)
        # 3) composite: 100% new fill inside; a wide, smooth transition OUTSIDE the shape only
        grown = shape.filter(ImageFilter.MaxFilter(min(feather * 2 + 1, 61)))
        outer = np.asarray(grown.filter(ImageFilter.GaussianBlur(feather)), dtype=np.float32) / 255.0
        outer = outer * outer * (3 - 2 * outer)
        alpha = np.maximum(inside, outer); alpha[inside > 0] = 1.0
        out[wy0:wy1, wx0:wx1] = np.clip(np.clip(big, 0, 255) * alpha[..., None] + win * (1 - alpha[..., None]) + 0.5, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGB")


def shape_pixels(region, size, kind="manual", angle=0.0, shape=None):
    """Boolean mask (full image size) of the pixels a region replaces at 100%."""
    r = {"box": region, "kind": kind, "angle": angle, "shape": shape}
    m = Image.new("L", size, 0); ImageDraw.Draw(m).polygon(_polygon(r, size, PAD.get(kind, 0.0)), fill=255)
    return np.asarray(m) > 0


def _detail(a: np.ndarray) -> np.ndarray:
    """Fine detail (what makes a face or text recognisable): the image minus its blurred self."""
    return a - _box_blur(a, 2)


def destroyed_fraction(original: Image.Image, redacted: Image.Image, region, kind="manual") -> float:
    """Correlation of FINE DETAIL between original and redacted pixels in the core of a region
    (0 = nothing recognisable left). Smooth light gradients are not information about the subject."""
    a = _detail(np.asarray(original.convert("L"), dtype=np.float32)); b = _detail(np.asarray(redacted.convert("L"), dtype=np.float32))
    H, W = a.shape; x0, y0, x1, y1 = to_pixels(region, (W, H), PAD.get(kind, 0.0))
    cx0, cy0 = x0 + (x1 - x0) // 4, y0 + (y1 - y0) // 4; cx1, cy1 = x1 - (x1 - x0) // 4, y1 - (y1 - y0) // 4
    pa, pb = a[cy0:cy1, cx0:cx1].ravel(), b[cy0:cy1, cx0:cx1].ravel()
    if pa.std() < 1e-6 or pb.std() < 1e-6:
        return 0.0
    return float(abs(np.corrcoef(pa, pb)[0, 1]))
