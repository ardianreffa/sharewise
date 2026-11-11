"""What a photo would reveal if shared, as yes/no categories only.

Wall T1: nothing in this module returns a metadata VALUE. No coordinates, no timestamps, no device
names, no serial numbers, no names, no comments: only booleans per category. Values are read inside
these functions and dropped. Nothing here touches the network or writes anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image

SUPPORTED = {".jpg", ".jpeg", ".jpe", ".heic", ".heif", ".hif", ".png", ".webp"}
CATEGORIES = ("location", "location_detail", "time", "device", "serials", "owner", "comments",
              "hidden_thumbnail", "edit_history", "depth_map", "copyright")
SENSITIVE = tuple(c for c in CATEGORIES if c != "copyright")      # copyright is kept on purpose (wall T4)

GPS_IFD, EXIF_IFD = 0x8825, 0x8769
T_TIME = {306, 36867, 36868, 36880, 36881, 36882, 37520, 37521, 37522}
T_DEVICE = {271, 272, 305, 316, 42035, 42036, 50708}          # make, model, software, host, lens make/model, unique camera model
T_SERIALS = {42033, 42037, 42016, 37500}                        # body/lens serial, image unique id, maker note
T_OWNER = {315, 42032, 40093}                                   # artist, camera owner, XP author
T_COMMENTS = {270, 37510, 40092, 40094, 40095}                  # description, user comment, XP comment/keywords/subject
COPYRIGHT_TAG = 33432
XMP_LOCATION = (b"GPSLatitude", b"GPSLongitude", b"photoshop:City", b"Iptc4xmpCore:Location", b"Iptc4xmpExt:LocationCreated",
                b"Iptc4xmpCore:CountryCode", b"photoshop:State", b"photoshop:Country")
XMP_TIME = (b"xmp:CreateDate", b"photoshop:DateCreated", b"exif:DateTimeOriginal", b"xmp:ModifyDate")
XMP_DEVICE = (b"tiff:Model", b"tiff:Make", b"aux:Lens", b"exifEX:LensModel", b"xmp:CreatorTool")
XMP_SERIALS = (b"aux:SerialNumber", b"exifEX:BodySerialNumber", b"aux:LensSerialNumber", b"xmpMM:DocumentID", b"xmpMM:InstanceID")
XMP_OWNER = (b"dc:creator", b"Iptc4xmpCore:CreatorContactInfo", b"photoshop:AuthorsPosition")
XMP_COMMENTS = (b"dc:description", b"dc:subject", b"exif:UserComment", b"photoshop:Headline")
XMP_HISTORY = (b"xmpMM:History", b"photoshop:History", b"crs:", b"xmpMM:DerivedFrom")
XMP_COPYRIGHT = (b"dc:rights", b"xmpRights:", b"photoshop:Copyright")
PNG_TEXT_LOCATION = ("gps", "location", "latitude", "longitude")


def _empty() -> dict:
    return {c: False for c in CATEGORIES}


def _from_exif(exif: Image.Exif, out: dict) -> None:
    keys = set(exif.keys())
    try:
        sub = exif.get_ifd(EXIF_IFD); keys |= set(sub.keys())
    except Exception:
        pass
    try:
        gps = exif.get_ifd(GPS_IFD)
    except Exception:
        gps = {}
    if gps:
        if {2, 4} & set(gps.keys()):                      # latitude or longitude
            out["location"] = True
        if set(gps.keys()) - {0, 1, 2, 3, 4}:              # altitude, speed, direction, timestamps, area...
            out["location_detail"] = True
    if 34853 in keys and not gps:                          # a GPS pointer with an unreadable block still counts
        out["location"] = True
    out["time"] |= bool(keys & T_TIME)
    out["device"] |= bool(keys & T_DEVICE)
    out["serials"] |= bool(keys & T_SERIALS)
    out["owner"] |= bool(keys & T_OWNER)
    out["comments"] |= bool(keys & T_COMMENTS)
    out["copyright"] |= COPYRIGHT_TAG in keys
    try:
        if exif.get_ifd(0x0001) or getattr(exif, "_ifd1", None):   # IFD1 = embedded thumbnail
            out["hidden_thumbnail"] = True
    except Exception:
        pass


def _from_xmp(xmp, out: dict) -> None:
    if not xmp:
        return
    b = xmp.encode("utf-8", "ignore") if isinstance(xmp, str) else bytes(xmp)
    has = lambda needles: any(n in b for n in needles)
    out["location"] |= has(XMP_LOCATION)
    out["time"] |= has(XMP_TIME)
    out["device"] |= has(XMP_DEVICE)
    out["serials"] |= has(XMP_SERIALS)
    out["owner"] |= has(XMP_OWNER)
    out["comments"] |= has(XMP_COMMENTS)
    out["edit_history"] |= has(XMP_HISTORY)
    out["copyright"] |= has(XMP_COPYRIGHT)


def _jpeg_segments(path: Path) -> dict:
    """Markers present in a JPEG header (APP1..APP15, COM, embedded thumbnails in APP1)."""
    seen = {"app": set(), "com": False, "exif_thumb": False, "photoshop": False}
    with open(path, "rb") as fh:
        if fh.read(2) != b"\xff\xd8":
            return seen
        while True:
            m = fh.read(2)
            if len(m) < 2 or m[0] != 0xFF:
                break
            if m[1] in (0xD9, 0xDA):
                break
            ln = int.from_bytes(fh.read(2), "big")
            data = fh.read(ln - 2)
            if 0xE0 <= m[1] <= 0xEF:
                seen["app"].add(m[1])
                if m[1] == 0xE1 and data.startswith(b"Exif") and b"\xff\xd8" in data[10:]:
                    seen["exif_thumb"] = True
                if m[1] == 0xED:
                    seen["photoshop"] = True
            elif m[1] == 0xFE:
                seen["com"] = True
    return seen


def findings(path: Path) -> dict:
    """Yes/no per category for one photo. Raises nothing; an unreadable file returns {'error': True}."""
    path = Path(path); ext = path.suffix.lower(); out = _empty()
    try:
        if ext in (".heic", ".heif", ".hif"):
            import pillow_heif                                    # opened directly, no global opener (lesson from Pickwise)
            h = pillow_heif.open_heif(str(path))
            if h.info.get("exif"):
                ex = Image.Exif(); raw = h.info["exif"]
                ex.load(raw[6:] if raw[:6] == b"Exif\x00\x00" else raw); _from_exif(ex, out)
            _from_xmp(h.info.get("xmp"), out)
            out["hidden_thumbnail"] |= bool(h.info.get("thumbnails") or getattr(h, "thumbnails", None))
            out["depth_map"] |= bool(h.info.get("depth_images") or h.info.get("aux"))
        else:
            with Image.open(path) as im:
                _from_exif(im.getexif(), out)
                _from_xmp(im.info.get("xmp") or im.info.get("XML:com.adobe.xmp"), out)
                if im.format == "PNG":
                    for k in (im.info or {}):
                        kl = str(k).lower()
                        if kl in ("exif", "icc_profile", "dpi", "gamma", "transparency", "srgb", "chromaticity", "aspect"):
                            continue
                        if any(w in kl for w in PNG_TEXT_LOCATION): out["location"] = True
                        elif "copyright" in kl: out["copyright"] = True
                        elif kl in ("author", "artist"): out["owner"] = True
                        elif kl in ("creation time", "date"): out["time"] = True
                        elif kl in ("software", "source", "model"): out["device"] = True
                        else: out["comments"] = True
            if ext in (".jpg", ".jpeg", ".jpe"):
                seg = _jpeg_segments(path)
                out["hidden_thumbnail"] |= seg["exif_thumb"]
                out["comments"] |= seg["com"]
                out["edit_history"] |= seg["photoshop"]
    except Exception:
        return {"error": True}
    return out


def risky(f: dict) -> list[str]:
    """Names of the sensitive categories that are present (still no values)."""
    return [c for c in SENSITIVE if f.get(c)]
