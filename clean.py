"""Make a clean copy of a photo for sharing. Remove-only; the original is never opened for writing.

Walls enforced here:
  T2  remove-only: the copy's metadata is a subset of the original's (orientation, copyright and what
      the user chose to keep). Nothing is invented, added or edited.
  T3  originals are read, never written; the copy is created exclusively (never overwrites anything).
  T4  copyright and licence statements are carried over.
  T7  every copy is re-read with meta.findings(); a copy that still reveals a category the user did not
      keep is deleted and reported as failed, never handed over as "clean".

JPEG: lossless. Header segments are rebuilt (EXIF/XMP/IPTC/comments/maker data dropped, ICC colour
profile and decoding segments kept) and the compressed image data is copied byte for byte; anything
appended after the image (Motion Photo video, MPF extra images) is dropped.
HEIC/HEIF: converted to JPEG (quality 92, orientation applied), because HEIC cannot be cleaned
losslessly here; depth maps, HDR gain maps and thumbnails are not carried over.
PNG: re-saved losslessly without text/EXIF chunks. WebP: re-encoded at quality 95 without metadata.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

from . import meta

KEEPABLE = ("time", "device", "location")
JPEG_EXT = (".jpg", ".jpeg", ".jpe")


class CleanError(Exception):
    pass


# ------------------------------------------------------------------ the kept EXIF (subset of the original)
def _rights_from_xmp(xmp) -> Optional[str]:
    if not xmp:
        return None
    s = xmp.decode("utf-8", "ignore") if isinstance(xmp, (bytes, bytearray)) else str(xmp)
    m = re.search(r"<dc:rights>.*?<rdf:li[^>]*>(.*?)</rdf:li>", s, re.S)
    return m.group(1).strip()[:300] if m else None


def kept_exif(src_exif: Image.Exif, keep: set, xmp=None, orientation: bool = True) -> Image.Exif:
    out = Image.Exif()
    if orientation and src_exif.get(274):
        out[274] = src_exif.get(274)
    if src_exif.get(meta.COPYRIGHT_TAG):
        out[meta.COPYRIGHT_TAG] = src_exif.get(meta.COPYRIGHT_TAG)
    else:
        r = _rights_from_xmp(xmp)
        if r:
            out[meta.COPYRIGHT_TAG] = r
    try:
        sub = src_exif.get_ifd(meta.EXIF_IFD)
    except Exception:
        sub = {}
    if "time" in keep:
        for t in meta.T_TIME:
            if t in src_exif: out[t] = src_exif[t]
        sub_keep = {t: v for t, v in sub.items() if t in meta.T_TIME}
        if sub_keep: out.get_ifd(meta.EXIF_IFD).update(sub_keep)
    if "device" in keep:
        for t in meta.T_DEVICE:
            if t in src_exif: out[t] = src_exif[t]
        sub_keep = {t: v for t, v in sub.items() if t in meta.T_DEVICE}
        if sub_keep: out.get_ifd(meta.EXIF_IFD).update(sub_keep)
    if "location" in keep:
        try:
            gps = src_exif.get_ifd(meta.GPS_IFD)
            if gps: out.get_ifd(meta.GPS_IFD).update(dict(gps))
        except Exception:
            pass
    return out


# ------------------------------------------------------------------ JPEG, lossless
def _jpeg_clean(src: Path, exif_bytes: Optional[bytes]) -> bytes:
    data = src.read_bytes()
    if data[:2] != b"\xff\xd8":
        raise CleanError("not a JPEG")
    out = bytearray(b"\xff\xd8"); i = 2; app0 = None; keep_segments = []
    while i < len(data):
        if data[i] != 0xFF:
            raise CleanError("damaged JPEG header")
        marker = data[i + 1]
        if marker == 0xDA:                                      # start of scan: image data follows
            break
        ln = int.from_bytes(data[i + 2:i + 4], "big"); seg = data[i:i + 2 + ln]; body = data[i + 4:i + 2 + ln]
        if marker == 0xE0 and body.startswith(b"JFIF"):
            app0 = seg
        elif marker == 0xE2 and body.startswith(b"ICC_PROFILE"):
            keep_segments.append(seg)                           # colour profile
        elif marker == 0xEE and body.startswith(b"Adobe"):
            keep_segments.append(seg)                           # needed to decode colours correctly
        elif 0xE0 <= marker <= 0xEF or marker == 0xFE:
            pass                                                # EXIF, XMP, IPTC, MPF, maker data, comments: dropped
        else:
            keep_segments.append(seg)                           # DQT, DHT, SOF, DRI...: image structure
        i += 2 + ln
    if app0: out += app0
    if exif_bytes:
        payload = b"Exif\x00\x00" + exif_bytes
        if len(payload) + 2 > 0xFFFF:
            raise CleanError("kept metadata too large")
        out += b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    for seg in keep_segments: out += seg
    end = data.find(b"\xff\xd9", i)                             # first end-of-image after the scan header
    if end < 0:
        raise CleanError("JPEG has no end marker")
    out += data[i:end + 2]                                      # compressed image, byte for byte; trailers dropped
    return bytes(out)


# ------------------------------------------------------------------ public
def dest_for(src: Path, out_dir: Path) -> Path:
    ext = ".jpg" if src.suffix.lower() in (".heic", ".heif", ".hif") else src.suffix.lower()
    cand = out_dir / f"{src.stem}-clean{ext}"; n = 2
    while cand.exists():
        cand = out_dir / f"{src.stem}-clean ({n}){ext}"; n += 1
    return cand


def clean_copy(src: Path, out_dir: Path, keep=()) -> Path:
    """Write a clean copy into out_dir and return its path. Raises CleanError; never leaves a bad copy behind."""
    src, out_dir = Path(src), Path(out_dir); keep = {k for k in keep if k in KEEPABLE}
    ext = src.suffix.lower()
    if ext not in meta.SUPPORTED:
        raise CleanError("unsupported file type")
    if out_dir.is_symlink():
        raise CleanError("output folder is a symlink")
    out_dir.mkdir(exist_ok=True)
    if shutil.disk_usage(out_dir).free < src.stat().st_size * 3 + 200 * 1024 ** 2:
        raise CleanError("not enough free disk space for the copy")
    dest = dest_for(src, out_dir)
    try:
        if ext in JPEG_EXT:
            with Image.open(src) as im:
                ex = im.getexif(); xmp = im.info.get("xmp")
            k = kept_exif(ex, keep, xmp)
            blob = _jpeg_clean(src, k.tobytes() if len(k) or k.get_ifd(meta.EXIF_IFD) or k.get_ifd(meta.GPS_IFD) else None)
            with open(dest, "xb") as fh:
                fh.write(blob)
        elif ext in (".heic", ".heif", ".hif"):
            import pillow_heif
            h = pillow_heif.open_heif(str(src), convert_hdr_to_8bit=True)
            ex = Image.Exif()
            if h.info.get("exif"):
                raw = h.info["exif"]; ex.load(raw[6:] if raw[:6] == b"Exif\x00\x00" else raw)
            im = h.to_pillow()                                    # pillow-heif has already applied the rotation
            im = ImageOps.exif_transpose(im).convert("RGB"); im.info.pop("exif", None)
            k = kept_exif(ex, keep, h.info.get("xmp"), orientation=False)
            kw = {"quality": 92, "subsampling": "4:2:0"}
            if h.info.get("icc_profile"): kw["icc_profile"] = h.info["icc_profile"]
            if len(k) or k.get_ifd(meta.EXIF_IFD) or k.get_ifd(meta.GPS_IFD): kw["exif"] = k.tobytes()
            with open(dest, "xb") as fh:
                im.save(fh, "JPEG", **kw)
        elif ext == ".png":
            from PIL.PngImagePlugin import PngInfo
            with Image.open(src) as im:
                im.load(); info = PngInfo(); kw = {}
                for key, val in (im.info or {}).items():
                    if "copyright" in str(key).lower() and isinstance(val, str): info.add_text(key, val)
                if im.info.get("icc_profile"): kw["icc_profile"] = im.info["icc_profile"]
                k = kept_exif(im.getexif(), keep, im.info.get("xmp"))
                if len(k) > (1 if 274 in k else 0) or k.get_ifd(meta.GPS_IFD): kw["exif"] = k.tobytes()
                clean = im.copy(); clean.info = {}
                with open(dest, "xb") as fh:
                    clean.save(fh, "PNG", pnginfo=info, **kw)
        elif ext == ".webp":
            with Image.open(src) as im:
                im.load(); kw = {"quality": 95, "method": 6}
                if im.info.get("icc_profile"): kw["icc_profile"] = im.info["icc_profile"]
                k = kept_exif(im.getexif(), keep, im.info.get("xmp"))
                if len(k) or k.get_ifd(meta.GPS_IFD): kw["exif"] = k.tobytes()
                clean = im.copy(); clean.info = {}
                with open(dest, "xb") as fh:
                    clean.save(fh, "WEBP", **kw)
        verify(dest, keep)
        return dest
    except Exception as exc:
        try:
            if dest.exists(): dest.unlink()                     # never leave a half-clean copy behind
        except OSError:
            pass
        raise exc if isinstance(exc, CleanError) else CleanError(type(exc).__name__)


def verify(dest: Path, keep: set) -> None:
    f = meta.findings(dest)
    if f.get("error"):
        raise CleanError("the copy could not be re-read")
    allowed = set(keep) | ({"location_detail"} if "location" in keep else set())
    left = [c for c in meta.SENSITIVE if f.get(c) and c not in allowed]
    if left:
        raise CleanError("verification failed: the copy still reveals " + ", ".join(left))
