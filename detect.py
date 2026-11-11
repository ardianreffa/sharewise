"""Find areas worth hiding, on this computer, with Apple's Vision framework (macOS only).

Faces, people whose face is too small or turned away (their head area is estimated), and text that
can locate someone: street names, house or unit numbers, licence plates. Other text (slides, shop
signs, menus) is proposed switched OFF, so the user can turn it on.

Wall T1 applies to text in the picture as well: recognised strings are classified in memory and
discarded. Only a box and a category ever leave this module, never the words.

No network: Vision runs on-device. On Linux and Windows automatic detection is unavailable and the
user can still draw areas by hand.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from PIL import Image

DETECT_EDGE = 3000            # Vision works on a downsized, metadata-free, upright copy

STREET = re.compile(r"\b(rd|road|st|street|ave|avenue|blvd|boulevard|lane|ln|drive|dr|way|place|pl|crescent|cres|"
                    r"jalan|jl|jln|gang|gg|lorong|lor|rue|strasse|straße|calle|avenida|via|jalur|komplek|perumahan|"
                    r"blok|blk|rt|rw|kampung|kp)\b\.?", re.I)
HOUSE = re.compile(r"^(no\.?\s*)?#?\d{1,5}[a-z]?([-/]\d{1,4}[a-z]?)?$|^(blk|block|blok|unit|no)\.?\s*[#\w-]*\d[#\w-]*$", re.I)
PLATE = re.compile(r"^[a-z]{1,3}\s?\d{1,4}\s?[a-z]{0,3}$", re.I)


class DetectUnavailable(Exception):
    pass


def available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import Vision  # noqa: F401
        return True
    except Exception:
        return False


def _classify(text: str) -> str:
    t = " ".join(text.split())
    compact = t.replace(" ", "")
    if STREET.search(t):
        return "street name"
    if PLATE.match(t) and any(c.isdigit() for c in t) and any(c.isalpha() for c in t) and 4 <= len(compact) <= 10:
        return "licence plate"
    if HOUSE.match(t):
        return "house or unit number"
    return "other text"


def _rect(o):
    bb = o.boundingBox()
    try:
        x, y, w, h = bb.origin.x, bb.origin.y, bb.size.width, bb.size.height
    except AttributeError:
        (x, y), (w, h) = bb
    return (float(x), float(1.0 - y - h), float(w), float(h))          # Vision's origin is bottom-left


def quad_to_box(tl, tr, br, bl, size):
    """Four corners (0..1, bottom-left origin, as Vision gives them) -> (x, y, w, h) top-left origin + angle in degrees."""
    import math
    W, H = size
    P = [(p[0] * W, (1 - p[1]) * H) for p in (tl, tr, br, bl)]               # pixels, top-left origin
    cx = sum(p[0] for p in P) / 4; cy = sum(p[1] for p in P) / 4
    w = (math.dist(P[0], P[1]) + math.dist(P[3], P[2])) / 2
    h = (math.dist(P[0], P[3]) + math.dist(P[1], P[2])) / 2
    ang = math.degrees(math.atan2(P[1][1] - P[0][1], P[1][0] - P[0][0]))
    return (cx / W - w / W / 2, cy / H - h / H / 2, w / W, h / H), ang


def _quad(o, size):
    try:
        pts = [o.topLeft(), o.topRight(), o.bottomRight(), o.bottomLeft()]
        return quad_to_box(*[(float(p.x), float(p.y)) for p in pts], size)
    except Exception:
        return _rect(o), 0.0


def _inside(a, b) -> bool:
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    cx, cy = ax + aw / 2, ay + ah / 2
    return bx <= cx <= bx + bw and by <= cy <= by + bh


TILE_EDGE = 2000              # faces are also searched tile by tile at higher resolution (crowds, small faces)
TILE_OVERLAP = 0.18


def tiles_for(size, edge=TILE_EDGE, overlap=TILE_OVERLAP):
    """Overlapping tiles (x, y, w, h in 0..1) covering a big photo; [] for small photos."""
    W, H = size
    if max(W, H) <= edge * 1.25:
        return []
    nx = max(1, round(W / (edge * (1 - overlap)))); ny = max(1, round(H / (edge * (1 - overlap))))
    tw, th = min(1.0, (1 + overlap) / nx), min(1.0, (1 + overlap) / ny)
    out = []
    for j in range(ny):
        for i in range(nx):
            x = 0.0 if nx == 1 else i * (1 - tw) / (nx - 1)
            y = 0.0 if ny == 1 else j * (1 - th) / (ny - 1)
            out.append((x, y, tw, th))
    return out


def from_tile(box, tile):
    bx, by, bw, bh = box; tx, ty, tw, th = tile
    return (tx + bx * tw, ty + by * th, bw * tw, bh * th)


def _iou(a, b):
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx)); iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy; union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def merge_faces(boxes):
    """One box per face when the whole photo and several tiles found the same face."""
    out = []
    for b in sorted(boxes, key=lambda b: -b[2] * b[3]):
        if any(_iou(b, o) > 0.25 or _inside(b, o) or _inside(o, b) for o in out):
            continue
        out.append(b)
    return out


def _vision(img: Image.Image, workdir: Path, faces_only: bool):
    """Run Apple Vision on one image; returns (face_boxes, human_boxes, [(box, text_kind)]) in that image's 0..1 space."""
    import Vision
    from Foundation import NSURL
    with tempfile.NamedTemporaryFile(suffix=".png", dir=workdir, delete=False) as fh:
        tmp = Path(fh.name)
    try:
        img.save(tmp, "PNG")                                             # upright, no metadata
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(tmp)), None)
        faces = Vision.VNDetectFaceRectanglesRequest.alloc().init(); reqs = [faces]
        humans = text = None
        if not faces_only:
            humans = Vision.VNDetectHumanRectanglesRequest.alloc().init()
            try:
                humans.setUpperBodyOnly_(False)
            except Exception:
                pass
            text = Vision.VNRecognizeTextRequest.alloc().init()
            text.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
            text.setUsesLanguageCorrection_(False)
            reqs += [humans, text]
        res = handler.performRequests_error_(reqs, None)
        ok = res[0] if isinstance(res, tuple) else bool(res)
        if not ok:
            raise DetectUnavailable("Apple Vision could not analyse this photo")
        f = [_rect(o) for o in (faces.results() or [])]
        h = [_rect(o) for o in (humans.results() or [])] if humans else []
        t = []
        for o in ((text.results() or []) if text else []):
            cands = o.topCandidates_(1)
            if cands:
                box, ang = _quad(o, img.size)
                t.append((box, ang, _classify(str(cands[0].string()))))   # the words are dropped right here
        return f, h, t
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def detect(upright: Image.Image, workdir: Path) -> list[dict]:
    """[{"box": (x,y,w,h), "kind": "face"|"head"|"text", "on": bool, "why": str}] in 0..1 upright coordinates.
    Faces are searched on the whole photo and again on overlapping high-resolution tiles."""
    if not available():
        raise DetectUnavailable("automatic detection needs macOS (Apple Vision); you can still draw areas yourself")
    workdir.mkdir(parents=True, exist_ok=True)
    full = upright.convert("RGB")
    small = full.copy(); small.thumbnail((DETECT_EDGE, DETECT_EDGE))
    faces, humans, texts = _vision(small, workdir, faces_only=False)
    W, H = full.size
    for tile in tiles_for(full.size):
        x, y, w, h = tile
        crop = full.crop((int(x * W), int(y * H), int((x + w) * W), int((y + h) * H)))
        crop.thumbnail((TILE_EDGE, TILE_EDGE))
        tf, _, _ = _vision(crop, workdir, faces_only=True)
        faces += [from_tile(b, tile) for b in tf]
    faces = merge_faces(faces)
    out = [{"box": b, "kind": "face", "on": True, "why": "face"} for b in faces]
    for body in humans:
        if any(_inside(f, body) for f in faces):
            continue
        bx, by, bw, bh = body                                            # no face found: suggest the head area, OFF
        hw, hh = bw * 0.46, min(bh * 0.17, bw * 0.62)
        out.append({"box": (bx + (bw - hw) / 2, by, hw, hh), "kind": "head", "on": False,
                    "why": "person, no face found (often facing away)"})
    for box, ang, why in texts:
        out.append({"box": box, "kind": "text", "on": why != "other text", "why": why, "angle": round(ang, 1), "shape": "rect"})
    return out
