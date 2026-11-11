"""Video metadata (MOV / MP4 / M4V / 3GP), without converting the video.

A MOV/MP4 file is a tree of boxes. Location, dates, device and software are stored in metadata boxes
(`udta`, `meta`, Apple's `com.apple.quicktime.*` keys, `©xyz`, `loci`...) and in the creation/modification
time fields of the movie, track and media headers. Sharewise makes a COPY and, inside the copy:
  * renames every metadata box to `free` and zero-fills its content (same size, so every offset in the
    file stays valid and the picture and sound are untouched, byte for byte);
  * sets the creation and modification times in the headers to zero.
Nothing is re-encoded. The copy is then re-read and must show no location, time or device data.
Like the photo side: yes/no only, never a value; originals are never written.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
from pathlib import Path

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".3gp", ".3g2"}
CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf", b"udta", b"meta", b"ilst", b"moof", b"traf", b"mvex"}
META_BOXES = {b"meta", b"uuid", b"XMP_", b"loci", b"\xa9xyz"}     # blanked wherever they appear
KEEP_IN_UDTA = {b"cprt", b"\xa9cpy"}                               # copyright stays (wall T4)
HEADERS = {b"mvhd", b"tkhd", b"mdhd"}
LOCATION_MARKS = (b"ISO6709", b"\xa9xyz", b"loci", b"location", b"GPS", b"gps")
TIME_MARKS = (b"creationdate", b"\xa9day", b"date")
DEVICE_MARKS = (b"quicktime.make", b"quicktime.model", b"quicktime.software", b"\xa9mak", b"\xa9mod", b"\xa9swr", b"\xa9too",
                b"encoder", b"com.android.version", b"manufacturer")


def _boxes(f, start: int, end: int):
    """Yield (type, box_start, header_size, box_end) for boxes between start and end."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos); hdr = f.read(8)
        if len(hdr) < 8:
            return
        size, typ = struct.unpack(">I4s", hdr); hsize = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]; hsize = 16
        elif size == 0:
            size = end - pos
        if size < hsize or pos + size > end:
            return
        yield typ, pos, hsize, pos + size
        pos += size


def _walk(f, start, end, depth=0, parent=b""):
    for typ, s, h, e in _boxes(f, start, end):
        yield typ, s, h, e, parent
        if typ in CONTAINERS and depth < 12:
            inner = s + h + (4 if typ == b"meta" and _is_full_meta(f, s + h) else 0)
            yield from _walk(f, inner, e, depth + 1, typ)


def _is_metadata(typ: bytes, parent: bytes) -> bool:
    if parent == b"udta":
        return typ not in KEEP_IN_UDTA                 # every user-data item except copyright
    return typ in META_BOXES


def _is_full_meta(f, pos) -> bool:
    """MP4 'meta' is a full box (4 bytes version/flags) before its children; QuickTime 'meta' is not."""
    f.seek(pos); b = f.read(8)
    return len(b) == 8 and b[:4] == b"\x00\x00\x00\x00" and b[4:8] != b"hdlr"


def _header_times(f, s, h) -> tuple[int, int, int]:
    """(offset of creation_time, field size, value) for mvhd/tkhd/mdhd; creation and modification are adjacent."""
    f.seek(s + h); ver = f.read(1)[0]
    if ver == 1:
        f.seek(s + h + 4); v = struct.unpack(">Q", f.read(8))[0]; return s + h + 4, 8, v
    f.seek(s + h + 4); v = struct.unpack(">I", f.read(4))[0]; return s + h + 4, 4, v


def _full(f, s, h):
    f.seek(s + h + 4)


def _child(f, s, e, path):
    """First box at the given path of child types below (s, e), e.g. [b"mdia", b"hdlr"]."""
    start, end = s, e
    for want in path:
        hit = None
        for typ, bs, bh, be in _boxes(f, start, end):
            if typ == want:
                hit = (bs, bh, be); break
        if hit is None:
            return None
        start, end = hit[0] + hit[1], hit[2]
        last = hit
    return last


def tracks(f, size) -> list[dict]:
    """Every track: {"handler", "format", "start", "end", "samples": [(offset, size)], "ok": bool}."""
    out = []
    moov = None
    for typ, s, h, e in _boxes(f, 0, size):
        if typ == b"moov":
            moov = (s + h, e); break
    if moov is None:
        return out
    for typ, s, h, e in _boxes(f, *moov):
        if typ != b"trak":
            continue
        hdlr = _child(f, s + h, e, [b"mdia", b"hdlr"])
        if hdlr is None:
            continue
        f.seek(hdlr[0] + hdlr[1] + 8); handler = f.read(4)
        stbl = _child(f, s + h, e, [b"mdia", b"minf", b"stbl"])
        if stbl is None:
            continue
        sb, sh, se = stbl
        stsd = _child(f, sb + sh, se, [b"stsd"])
        fmt = b""
        if stsd:
            f.seek(stsd[0] + stsd[1] + 8 + 4); fmt = f.read(4)
        samples = []; ok = True
        try:
            stsz = _child(f, sb + sh, se, [b"stsz"]); stsc = _child(f, sb + sh, se, [b"stsc"])
            stco = _child(f, sb + sh, se, [b"stco"]) or _child(f, sb + sh, se, [b"co64"])
            f.seek(stsz[0] + stsz[1] + 4); fixed, count = struct.unpack(">II", f.read(8))
            sizes = [fixed] * count if fixed else list(struct.unpack(f">{count}I", f.read(4 * count)))
            f.seek(stsc[0] + stsc[1] + 4); n = struct.unpack(">I", f.read(4))[0]
            runs = [struct.unpack(">III", f.read(12)) for _ in range(n)]
            f.seek(stco[0] + 4); is64 = f.read(4) == b"co64"
            f.seek(stco[0] + stco[1] + 4); nc = struct.unpack(">I", f.read(4))[0]
            offs = list(struct.unpack(f">{nc}{'Q' if is64 else 'I'}", f.read(nc * (8 if is64 else 4))))
            k = 0
            for ci, off in enumerate(offs, start=1):
                per = next((spc for first, spc, _ in reversed(runs) if first <= ci), 0)
                pos = off
                for _ in range(per):
                    if k >= len(sizes): break
                    samples.append((pos, sizes[k])); pos += sizes[k]; k += 1
            if k != len(sizes):
                ok = False
        except Exception:
            samples = []; ok = False
        out.append({"handler": handler, "format": fmt, "start": s, "end": e, "samples": samples, "ok": ok})
    return out


def timed_metadata_tracks(f, size) -> list[tuple[int, int, list[tuple[int, int]]]]:
    """Tracks that carry timed metadata (handler 'meta' or sample entry 'mebx', e.g. iPhone face data and IDs)."""
    return [(t["start"], t["end"], t["samples"]) for t in tracks(f, size) if t["handler"] == b"meta" or t["format"] == b"mebx"]


MARKERS = (b"com.apple.quicktime.location", b"ISO6709", b"detected-face", b"quicktime.make", b"quicktime.model",
           b"quicktime.software", b"quicktime.creationdate", b"apple-maker-note", b"\xa9xyz", b"\xa9mod", b"\xa9mak")
COORD = re.compile(rb"[+-]\d{2}\.\d{3,}[+-]\d{3}\.\d{3,}")


def byte_scan(path: Path) -> list[str]:
    """Whole-file scan for metadata that hides outside the official boxes. Returns category names only."""
    found = set(); tail = b""
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            buf = tail + chunk
            if COORD.search(buf) or b"ISO6709" in buf or b"quicktime.location" in buf or b"\xa9xyz" in buf: found.add("location")
            if b"detected-face" in buf: found.add("face_data")
            if any(m in buf for m in (b"quicktime.make", b"quicktime.model", b"quicktime.software", b"apple-maker-note",
                                      b"\xa9mod", b"\xa9mak")): found.add("device")
            if b"quicktime.creationdate" in buf: found.add("time")
            tail = buf[-64:]
    return sorted(found)


ORPHAN_LIMIT = 0.03            # refuse (rather than risk the picture) if more of the media area is unreferenced


def findings(path: Path) -> dict:
    """Yes/no per category for one video. Never returns a value."""
    out = {"location": False, "time": False, "device": False, "other_metadata": False, "copyright": False,
           "face_data": False, "serials": False}
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            for _s, _e, samples in timed_metadata_tracks(f, size):     # per-frame face data, IDs, live-photo links
                out["face_data"] = True; out["serials"] = True
            for typ, s, h, e, parent in _walk(f, 0, size):
                if typ in HEADERS:
                    off, n, v = _header_times(f, s, h)
                    f.seek(off + n); mod = struct.unpack(">Q" if n == 8 else ">I", f.read(n))[0]
                    if v or mod:
                        out["time"] = True
                elif typ == b"free":
                    continue
                elif _is_metadata(typ, parent):
                    f.seek(s); blob = f.read(min(e - s, 4 * 1024 * 1024))
                    if any(m in blob for m in LOCATION_MARKS): out["location"] = True
                    if any(m in blob for m in TIME_MARKS): out["time"] = True
                    if any(m in blob for m in DEVICE_MARKS): out["device"] = True
                    if blob.strip(b"\x00")[8:]: out["other_metadata"] = True
                elif parent == b"udta" and typ in KEEP_IN_UDTA:
                    out["copyright"] = True
    except Exception:
        return {"error": True}
    return out


def clean_copy(src: Path, out_dir: Path) -> Path:
    """Copy the video and blank its metadata in the copy. Raises ValueError; never leaves a bad copy behind."""
    src, out_dir = Path(src), Path(out_dir)
    if src.suffix.lower() not in VIDEO_EXT:
        raise ValueError("unsupported video type")
    out_dir.mkdir(exist_ok=True)
    if shutil.disk_usage(out_dir).free < src.stat().st_size * 2 + 300 * 1024 ** 2:
        raise ValueError("not enough free disk space for the copy")
    dest = out_dir / f"{src.stem}-clean{src.suffix.lower()}"; n = 2
    while dest.exists():
        dest = out_dir / f"{src.stem}-clean ({n}){src.suffix.lower()}"; n += 1
    try:
        with open(src, "rb") as fi, open(dest, "xb") as fo:
            shutil.copyfileobj(fi, fo, 4 * 1024 * 1024)
        size = dest.stat().st_size
        with open(dest, "r+b") as f:
            blank, times = [], []
            padding = []
            for typ, s, h, e, parent in _walk(f, 0, size):
                if typ in HEADERS:
                    off, n_, _v = _header_times(f, s, h); times.append((off, n_ * 2))
                elif typ in (b"free", b"skip", b"wide"):
                    padding.append((s, h, e))           # padding is never read by players, but can hold stale headers
                elif _is_metadata(typ, parent):
                    blank.append((s, h, e))
            all_tracks = tracks(f, size)
            if not all_tracks or not all(t["ok"] for t in all_tracks):
                raise ValueError("unusual video structure (sample tables could not be read); not cleaned")
            keep = sorted((o, o + n_) for t in all_tracks if not (t["handler"] == b"meta" or t["format"] == b"mebx")
                          for o, n_ in t["samples"])
            for t in all_tracks:                        # timed metadata tracks: dropped from the movie
                if t["handler"] == b"meta" or t["format"] == b"mebx":
                    blank.append((t["start"], 8, t["end"]))
            for typ, ms, mh, me in list(_boxes(f, 0, size)):
                if typ != b"mdat":
                    continue
                # everything in the media area that no picture/sound track uses is zeroed: per-frame face data,
                # IDs, and orphaned metadata blocks some phones leave in the media area
                gaps, pos = [], ms + mh
                for a, b_ in keep:
                    if b_ <= pos or a >= me:
                        continue
                    if a > pos: gaps.append((pos, a))
                    pos = max(pos, b_)
                if pos < me: gaps.append((pos, me))
                orphan = sum(b_ - a for a, b_ in gaps)
                if orphan > (me - ms - mh) * ORPHAN_LIMIT:
                    raise ValueError("unusual video structure (too much unreferenced data); not cleaned, to protect the video")
                for a, b_ in gaps:
                    f.seek(a); left = b_ - a
                    while left:
                        n_ = min(left, 1024 * 1024); f.write(b"\x00" * n_); left -= n_
            for s, h, e in blank:                       # outermost first is fine: inner boxes are inside the same range
                f.seek(s + 4); f.write(b"free")
                f.seek(s + h); f.write(b"\x00" * (e - s - h))
            for off, n_ in times:
                f.seek(off); f.write(b"\x00" * n_)
            for s, h, e in padding:
                f.seek(s + h); left = e - s - h
                while left > 0:
                    n_ = min(left, 1024 * 1024); f.write(b"\x00" * n_); left -= n_
        left = findings(dest)
        bad = sorted({k for k in ("location", "time", "device", "face_data", "serials") if left.get(k)} | set(byte_scan(dest)))
        if left.get("error") or bad:
            raise ValueError("verification failed: the copy still carries " + ", ".join(bad))
        return dest
    except Exception:
        if dest.exists():
            dest.unlink()
        raise
