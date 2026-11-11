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


def _inside(a, b) -> bool:
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    cx, cy = ax + aw / 2, ay + ah / 2
    return bx <= cx <= bx + bw and by <= cy <= by + bh


def detect(upright: Image.Image, workdir: Path) -> list[dict]:
    """[{"box": (x,y,w,h), "kind": "face"|"head"|"text", "on": bool, "why": str}] in 0..1 upright coordinates."""
    if not available():
        raise DetectUnavailable("automatic detection needs macOS (Apple Vision); you can still draw areas yourself")
    import Vision
    from Foundation import NSURL
    small = upright.convert("RGB").copy(); small.thumbnail((DETECT_EDGE, DETECT_EDGE))
    workdir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".png", dir=workdir, delete=False) as fh:
        tmp = Path(fh.name)
    try:
        small.save(tmp, "PNG")                                           # upright, no metadata
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(tmp)), None)
        faces = Vision.VNDetectFaceRectanglesRequest.alloc().init()
        humans = Vision.VNDetectHumanRectanglesRequest.alloc().init()
        try:
            humans.setUpperBodyOnly_(False)
        except Exception:
            pass
        text = Vision.VNRecognizeTextRequest.alloc().init()
        text.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        text.setUsesLanguageCorrection_(False)
        res = handler.performRequests_error_([faces, humans, text], None)
        ok = res[0] if isinstance(res, tuple) else bool(res)
        if not ok:
            raise DetectUnavailable("Apple Vision could not analyse this photo")
        out = []
        face_boxes = [_rect(o) for o in (faces.results() or [])]
        out += [{"box": b, "kind": "face", "on": True, "why": "face"} for b in face_boxes]
        for o in (humans.results() or []):
            body = _rect(o)
            if any(_inside(f, body) for f in face_boxes):
                continue
            bx, by, bw, bh = body                                        # no visible face: hide where the head is
            hw, hh = bw * 0.46, min(bh * 0.17, bw * 0.62)
            out.append({"box": (bx + (bw - hw) / 2, by, hw, hh), "kind": "head", "on": True, "why": "person (head area)"})
        for o in (text.results() or []):
            cands = o.topCandidates_(1)
            if not cands:
                continue
            why = _classify(str(cands[0].string()))                   # the words are dropped right here
            out.append({"box": _rect(o), "kind": "text", "on": why != "other text", "why": why})
        return out
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
