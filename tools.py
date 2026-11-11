"""Sharewise: tool handlers.

Roles, enforced here:
  * The plugin reads, detects, hides and verifies. It never reports a metadata value or a word read in a photo.
  * The agent explains and runs the steps. It passes only check ids, photo numbers and fixed choices (T9).
    It may ADD areas to hide; it can never switch an area off (R4): switching off needs the selection code
    the user copies from the review page.
  * The user decides. Keeping location needs Hermes' approval prompt (T8).
Copies with hidden areas are re-encoded, cleaned and verified before they are handed over (R3), and the
agent's optional visual check only ever sees those finished, clean copies.
No network access, no model calls, no shell commands, no process-wide settings changed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageOps

from . import clean, detect, guard, meta, redact, review, video

PLUGIN = "sharewise"
MAX_PHOTOS = 200
REVIEW_BATCH = 12
KEEP_CHECKS = 10
LABEL = {"location": "exact location (GPS)", "location_detail": "altitude, speed or direction",
         "time": "exact date and time", "device": "phone or camera model", "serials": "unique device or photo IDs",
         "owner": "owner or author name", "comments": "captions or comments", "hidden_thumbnail": "a hidden thumbnail of the original",
         "edit_history": "editing history", "depth_map": "a depth map / extra images",
         "face_data": "per-frame face positions recorded by the camera (videos)"}
CODE = re.compile(r"^SW1\.([0-9a-f]{8})\.(\d{1,4})\.([0-9,]+|none)\.((?:\d{12})(?:-\d{12})*|none)\.([0-9a-z]{1,4})$")
APPROVAL_WINDOW = 600.0


def _ok(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _err(message: str, **extra: Any) -> str:
    return json.dumps({"error": message, **extra}, ensure_ascii=False)


def _session_dir() -> Path:
    try:
        from agent.runtime_cwd import resolve_agent_cwd
        return Path(resolve_agent_cwd())
    except Exception:
        return Path.home()


def _display(p: str) -> str:
    home = str(Path.home())
    return "~" + p[len(home):] if p.startswith(home) else p


def code_checksum(body: str) -> str:
    h = 0
    for c in body:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    n = h % 46656; digits = "0123456789abcdefghijklmnopqrstuvwxyz"; out = ""
    while True:
        out = digits[n % 36] + out; n //= 36
        if not n: break
    return out


def parse_code(code: str) -> Optional[dict]:
    m = CODE.match((code or "").strip())
    if not m:
        return None
    check, photo, ids, boxes, chk = m.groups()
    body = f"{check}.{photo}.{ids}.{boxes}"
    if code_checksum(body) != chk:
        return None
    on = [] if ids == "none" else [int(i) for i in ids.split(",") if i]
    manual = []
    if boxes != "none":
        for b in boxes.split("-"):
            v = [int(b[i:i + 3]) / 1000 for i in range(0, 12, 3)]
            if v[2] > 0 and v[3] > 0:
                manual.append(tuple(v))
    return {"check": check, "photo": int(photo), "on": on, "manual": manual}


def load_upright(path: Path) -> Image.Image:
    ext = path.suffix.lower()
    if ext in (".heic", ".heif", ".hif"):
        import pillow_heif
        # pillow-heif already applies the HEIF rotation when decoding and marks the image upright; the raw EXIF
        # orientation must NOT be applied a second time (that turned portrait photos sideways)
        im = pillow_heif.open_heif(str(path), convert_hdr_to_8bit=True).to_pillow()
        return ImageOps.exif_transpose(im).convert("RGB")
    with Image.open(path) as im:
        return ImageOps.exif_transpose(im).convert("RGB")


def _grid(im: Image.Image) -> Image.Image:
    """A copy with thin labelled lines every 10%, so a vision model can name positions (0..1, top-left origin)."""
    from PIL import ImageDraw, ImageFont
    g = im.convert("RGB").copy(); d = ImageDraw.Draw(g); W, H = g.size
    try:
        font = ImageFont.load_default(size=max(12, W // 70))
    except TypeError:
        font = ImageFont.load_default()
    for i in range(1, 10):
        x, y = int(W * i / 10), int(H * i / 10)
        d.line((x, 0, x, H), fill=(255, 220, 0), width=1); d.line((0, y, W, y), fill=(255, 220, 0), width=1)
        for (tx, ty) in ((x + 3, 2), (x + 3, H - 18)):
            d.text((tx, ty), f"{i / 10:.1f}", fill=(255, 220, 0), font=font, stroke_width=2, stroke_fill=(0, 0, 0))
        for (tx, ty) in ((3, y + 2), (W - 34, y + 2)):
            d.text((tx, ty), f"{i / 10:.1f}", fill=(255, 220, 0), font=font, stroke_width=2, stroke_fill=(0, 0, 0))
    return g


class Sharewise:
    def __init__(self, ctx):
        self.ctx = ctx
        self._db = None
        self._approvals: dict[str, float] = {}

    # ── storage ─────────────────────────────────────────────────────────
    def _conn(self):
        if self._db is None:
            from plugins.plugin_storage import plugin_db
            self._db = plugin_db(PLUGIN)
            self._db.execute("CREATE TABLE IF NOT EXISTS checks (id TEXT PRIMARY KEY, created REAL, data TEXT)")
            self._db.commit()
        return self._db

    def _data_dir(self) -> Path:
        from plugins.plugin_storage import plugin_data_dir
        return plugin_data_dir(PLUGIN)

    def _state_get(self, key, default=None):
        try:
            return self.ctx.state.get(key, default=default)
        except TypeError:
            return self.ctx.state.get(key, default)

    def _state_set(self, key, value):
        self.ctx.state.set(key, value)

    def _save(self, c: dict) -> None:
        db = self._conn()
        db.execute("INSERT OR REPLACE INTO checks(id, created, data) VALUES (?,?,?)", (c["id"], c["created"], json.dumps(c)))
        old = [r[0] for r in db.execute("SELECT id FROM checks ORDER BY created DESC LIMIT -1 OFFSET ?", (KEEP_CHECKS,))]
        for oid in old:
            db.execute("DELETE FROM checks WHERE id = ?", (oid,))
            shutil.rmtree(self._data_dir() / "review" / oid, ignore_errors=True)
        db.commit()

    def _load(self, cid: Optional[str]) -> Optional[dict]:
        cid = cid or self._state_get("last_check")
        if not cid:
            return None
        row = self._conn().execute("SELECT data FROM checks WHERE id = ?", (cid,)).fetchone()
        return json.loads(row[0]) if row else None

    # ── 1. check: what would these photos reveal? (yes/no only) ─────────
    def check(self, args: dict, **_: Any) -> str:
        verdict = guard.check(str(args.get("folder") or ""), _session_dir())
        recursive = bool(args.get("include_subfolders"))
        if not verdict.allowed:
            return _err(f"refused: {verdict.reason}", requested={"folder": args.get("folder")},
                        rule="Sharewise only reads folders inside the home folder or on external drives. Ask the user; do not try another path.")
        root = verdict.path; skipped: dict = {}; photos = []; other_media = 0
        from . import scan_walk
        for p, st in scan_walk.walk(root, recursive, skipped):
            ext = p.suffix.lower()
            if ext in meta.SUPPORTED or ext in video.VIDEO_EXT:
                if len(photos) < MAX_PHOTOS:
                    photos.append(str(p))
            elif ext in scan_walk.OTHER_MEDIA:
                other_media += 1
        if not photos:
            return _err("no supported photos (JPEG, HEIC, PNG, WebP) or videos (MOV, MP4, M4V, 3GP) in that folder",
                        scanned_exactly=_display(str(root)))
        items = []
        for p in photos:
            is_video = Path(p).suffix.lower() in video.VIDEO_EXT
            f = video.findings(Path(p)) if is_video else meta.findings(Path(p))
            items.append({"path": p, "video": is_video, "findings": {k: bool(v) for k, v in f.items()}})
        c = {"id": uuid.uuid4().hex[:8], "created": time.time(), "folder": str(root), "include_subfolders": recursive,
             "photos": items, "areas": {}, "outputs": {}}
        self._save(c); self._state_set("last_check", c["id"])
        counts = {k: sum(1 for i in items if i["findings"].get(k)) for k in meta.SENSITIVE + ("face_data",)}
        n_video = sum(1 for i in items if i["video"])
        return _ok({
            "check_id": c["id"], "scanned_exactly": _display(str(root)), "include_subfolders": recursive,
            "photos": len(items) - n_video, "videos": n_video,
            "photo_names": [f"{n + 1}. {Path(i['path']).name}" for n, i in enumerate(items[:40])],
            "what_they_reveal": {LABEL[k]: f"{v} of {len(items)} files" for k, v in counts.items() if v},
            **({"videos_note": "Videos: location, dates and device are removed from the copies without re-encoding. Faces and "
                               "signs inside a video are NOT hidden."} if n_video else {}),
            "copyright_notices_kept": sum(1 for i in items if i["findings"].get("copyright")),
            "not_checked": {**({"RAW, TIFF and other formats (they can carry location too)": other_media} if other_media else {}),
                            **{k: v[0] for k, v in skipped.items()}},
            "note": "Only yes/no is reported: Sharewise never reads out a location, a time, a device or any text in a photo.",
            "rules_for_you": ("Explain the risks in plain words without guessing values. Offer sharewise_review to look for faces, "
                              "street names, house numbers and plates. Clean copies are made only with sharewise_share."),
        })

    # ── 2. review: detect areas, build pages ────────────────────────────
    def review(self, args: dict, **_: Any) -> str:
        extra = sorted(set(args) - {"check_id", "photos"})
        if extra:
            return _err(f"only check_id and photos are accepted (got {extra})")
        c = self._load(args.get("check_id"))
        if c is None:
            return _err("no check found; run sharewise_check first")
        nums = args.get("photos") or list(range(1, len(c["photos"]) + 1))
        nums = [n for n in nums if isinstance(n, int) and 1 <= n <= len(c["photos"])][:REVIEW_BATCH]
        out_dir = self._data_dir() / "review" / c["id"]; pages = []; summary = {}; unavailable = None
        for n in nums:
            item = c["photos"][n - 1]; src = Path(item["path"])
            if item.get("video"):
                summary[f"{n}. {src.name}"] = "video: metadata only, faces and signs in videos are not hidden"; continue
            try:
                up = load_upright(src)
            except Exception:
                summary[f"{n}. {src.name}"] = "could not be opened"; continue
            try:
                found = detect.detect(up, self._data_dir() / "tmp")
            except detect.DetectUnavailable as exc:
                found, unavailable = [], str(exc)
            areas = [{"id": i, "box": tuple(a["box"]), "kind": a["kind"], "on": a["on"], "why": a["why"]} for i, a in enumerate(found)]
            c["areas"][str(n)] = areas
            page = review.build(up, areas, c["id"], n, src.name, out_dir)
            pages.append(str(page))
            kinds: dict = {}
            for a in areas:
                key = a["why"] + ("" if a["on"] else " (off, user can switch on)")
                kinds[key] = kinds.get(key, 0) + 1
            summary[f"{n}. {src.name}"] = kinds or "nothing detected"
        self._save(c)
        return _ok({"check_id": c["id"], "review_pages": pages, "found": summary,
                    **({"automatic_detection": unavailable} if unavailable else {}),
                    "how": ("Give the user the page paths to open in a browser. On each page they click areas off or on and drag "
                            "to add their own, then copy the selection code back to you. Detection can miss things and can be wrong: "
                            "say so, and never promise that every face or sign was found."),
                    "privacy": "Pages and their downsized photos stay in Sharewise's data folder on this computer."})

    # ── 3. share: hide areas, clean, verify ─────────────────────────────
    KEEPABLE = ("time", "device", "location")

    def _validate_share(self, args: dict):
        extra = sorted(set(args) - {"check_id", "keep", "selection_codes", "add_areas", "photos"})
        if extra:
            return None, f"only check_id, photos, keep, selection_codes and add_areas are accepted (got {extra}); never file paths"
        c = self._load(args.get("check_id"))
        if c is None or (args.get("check_id") and args["check_id"] != c["id"]):
            return None, "unknown or missing check_id; run sharewise_check first"
        keep = args.get("keep") or []
        if isinstance(keep, str): keep = [keep]
        bad = [k for k in keep if k not in self.KEEPABLE]
        if bad:
            return None, f"keep can only contain {list(self.KEEPABLE)} (got {bad}); everything else is always removed"
        codes = args.get("selection_codes") or []
        if isinstance(codes, str): codes = [codes]
        parsed = []
        for code in codes:
            p = parse_code(code)
            if p is None or p["check"] != c["id"] or not (1 <= p["photo"] <= len(c["photos"])):
                return None, "a selection code is not valid for this check; ask the user to copy it again from the review page"
            parsed.append(p)
        adds = args.get("add_areas") or []
        if not isinstance(adds, list) or len(adds) > 60:
            return None, "add_areas must be a list of at most 60 areas"
        for a in adds:
            if not (isinstance(a, dict) and set(a) <= {"photo", "box"} and isinstance(a.get("photo"), int)
                    and 1 <= a["photo"] <= len(c["photos"]) and isinstance(a.get("box"), (list, tuple)) and len(a["box"]) == 4
                    and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in a["box"]) and a["box"][2] > 0 and a["box"][3] > 0):
                return None, "each added area needs a photo number and a box [x, y, width, height] between 0 and 1"
        nums = args.get("photos") or list(range(1, len(c["photos"]) + 1))
        nums = [n for n in nums if isinstance(n, int) and 1 <= n <= len(c["photos"])]
        return {"check": c, "keep": set(keep), "codes": parsed, "adds": adds, "photos": nums}, None

    def selection_key(self, args: dict) -> str:
        return json.dumps({k: args.get(k) for k in sorted(args)}, sort_keys=True, default=str)

    def approval_request(self, args: dict) -> Optional[dict]:
        """pre_tool_call hook: block invalid requests; send 'keep location' to Hermes' approval gate (T8)."""
        sel, error = self._validate_share(args or {})
        if error:
            return {"action": "block", "message": f"Sharewise: {error}"}
        self._approvals[self.selection_key(args)] = time.time()
        if "location" in sel["keep"]:
            return {"action": "approve",
                    "message": (f"Sharewise will make {len(sel['photos'])} {'copy' if len(sel['photos']) == 1 else 'copies'} that KEEP "
                                "the exact location where each photo was taken. Anyone who receives them can see that place on a map."),
                    "rule_key": f"sharewise_keep_location:{uuid.uuid4().hex[:8]}"}
        return None

    def share(self, args: dict, **_: Any) -> str:
        key = self.selection_key(args)
        reached = self._approvals.pop(key, None)
        if reached is None or time.time() - reached > APPROVAL_WINDOW:
            return _err("refused: this request did not pass Sharewise's pre_tool_call check; nothing was written")
        sel, error = self._validate_share(args)
        if error:
            return _err(error)
        c, keep = sel["check"], sel["keep"]
        out_dir = Path(c["folder"]) / "Sharewise"
        if out_dir.exists() and (out_dir.is_symlink() or not out_dir.is_dir()):
            return _err("the Sharewise output folder is not a plain folder; nothing was written")
        codes = {p["photo"]: p for p in sel["codes"]}
        adds: dict = {}
        for a in sel["adds"]:
            adds.setdefault(a["photo"], []).append(tuple(float(v) for v in a["box"]))
        made, failed, hidden_total = [], [], 0
        for n in sel["photos"]:
            item = c["photos"][n - 1]; src = Path(item["path"])
            areas = c["areas"].get(str(n), [])
            if n in codes:                                   # the user's own choices from the review page
                on = set(codes[n]["on"])
                regions = [{"box": a["box"], "kind": a["kind"]} for a in areas if a["id"] in on]
                regions += [{"box": b, "kind": "manual"} for b in codes[n]["manual"]]
            else:                                            # default: everything the detector proposed as "on"
                regions = [{"box": a["box"], "kind": a["kind"]} for a in areas if a["on"]]
            regions += [{"box": b, "kind": "manual"} for b in adds.get(n, [])]     # R4: the agent can only add
            prev = c["outputs"].get(str(n))
            try:
                if prev and Path(prev).exists() and Path(prev).parent == out_dir:
                    Path(prev).unlink()                      # replace this check's own earlier copy, nothing else
                if item.get("video"):
                    dest = video.clean_copy(src, out_dir)   # all metadata blanked; keep options do not apply to videos
                    c["outputs"][str(n)] = str(dest)
                    made.append({"photo": n, "copy": dest.name, "video": True, "areas_hidden": 0}); continue
                if regions:
                    dest = self._hidden_copy(src, out_dir, regions, keep)
                else:
                    dest = clean.clean_copy(src, out_dir, keep)
                c["outputs"][str(n)] = str(dest); hidden_total += len(regions)
                made.append({"photo": n, "copy": dest.name, "areas_hidden": len(regions)})
            except (clean.CleanError, ValueError) as exc:
                failed.append({"photo": n, "name": src.name, "why": str(exc)})
            except Exception as exc:
                failed.append({"photo": n, "name": src.name, "why": type(exc).__name__})
        self._save(c)
        return _ok({"copies": len(made), "folder": _display(str(out_dir)), "made": made[:40], "failed": failed[:20],
                    "verified": "every copy was re-read: no location" + ("" if "location" not in keep else " except the kept location") +
                                ", no hidden thumbnails or depth maps, no IDs, names or comments" +
                                (", kept: " + ", ".join(sorted(keep)) if keep else ""),
                    "areas_hidden": hidden_total,
                    "tell_the_user": "Share the files in the Sharewise folder, not the originals. Originals were not changed.",
                    "next": "If the user asks for a visual check, call sharewise_look: it returns these finished copies only."})

    def _hidden_copy(self, src: Path, out_dir: Path, regions: list, keep: set) -> Path:
        """R3: hide areas, then save through the same rules as a clean copy, then verify."""
        out_dir.mkdir(exist_ok=True)
        if shutil.disk_usage(out_dir).free < src.stat().st_size * 4 + 300 * 1024 ** 2:
            raise clean.CleanError("not enough free disk space for the copy")
        up = load_upright(src)
        done = redact.redact(up, regions)
        ex_src = Image.Exif(); xmp = None; icc = None
        try:
            if src.suffix.lower() in (".heic", ".heif", ".hif"):
                import pillow_heif
                h = pillow_heif.open_heif(str(src)); raw = h.info.get("exif"); xmp = h.info.get("xmp"); icc = h.info.get("icc_profile")
                if raw: ex_src.load(raw[6:] if raw[:6] == b"Exif\x00\x00" else raw)
            else:
                with Image.open(src) as im:
                    ex_src = im.getexif(); xmp = im.info.get("xmp"); icc = im.info.get("icc_profile")
        except Exception:
            pass
        k = clean.kept_exif(ex_src, keep, xmp, orientation=False)       # pixels are already upright
        dest = out_dir / f"{src.stem}-shared.jpg"; i = 2
        while dest.exists():
            dest = out_dir / f"{src.stem}-shared ({i}).jpg"; i += 1
        kw = {"quality": 92, "subsampling": "4:2:0"}
        if icc: kw["icc_profile"] = icc
        if len(k) or k.get_ifd(meta.EXIF_IFD) or k.get_ifd(meta.GPS_IFD): kw["exif"] = k.tobytes()
        try:
            with open(dest, "xb") as fh:
                done.save(fh, "JPEG", **kw)
            clean.verify(dest, keep)
        except Exception:
            if dest.exists(): dest.unlink()
            raise
        return dest

    # ── 4. look: the agent's optional visual check, on finished copies only ──
    def look(self, args: dict, **_: Any) -> str:
        c = self._load(args.get("check_id"))
        if c is None or not c["outputs"]:
            return _err("no finished copies yet; run sharewise_share first")
        d = self._data_dir() / "look" / c["id"]; shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
        files = []
        for n, p in sorted(c["outputs"].items(), key=lambda kv: int(kv[0])):
            if not Path(p).exists() or Path(p).suffix.lower() in video.VIDEO_EXT: continue
            with Image.open(p) as im:
                small = im.convert("RGB"); small.thumbnail((1280, 1280))
            q = d / f"{int(n):03d}.jpg"; small.save(q, "JPEG", quality=85)        # no metadata at all (for the user)
            g = d / f"{int(n):03d}-grid.jpg"; _grid(small).save(g, "JPEG", quality=85)   # same picture with a 0..1 grid (for you)
            files.append({"photo": int(n), "image": str(g), "clean_copy_preview": str(q)})
        return _ok({"images": files[:12],
                    "how": ("Use your vision tool on 'image' (the finished copy with a grid; lines every 0.1, labelled at the "
                            "edges, origin top-left). Either the user asked you to hide something specific (e.g. 'the woman in the "
                            "hat on the left'), or they asked for a visual check for anything still identifying (a face, a house "
                            "number, a street sign, a plate, a name badge, a screen). Read the area's position off the grid as "
                            "[x, y, width, height] in 0..1, a little generous around a face, and call sharewise_share again with "
                            "add_areas for that photo. Then tell the user which area you added and that they can fine-tune it on "
                            "the review page (drag to move, corner to resize). Never claim certainty about a position."),
                    "privacy": "These images are sent to your model provider when you look at them."})

    # ── slash command: literal, no model ─────────────────────────────────
    def slash(self, raw: str = "", **_: Any) -> str:
        parts = (raw or "").strip().split(None, 1); sub = parts[0].lower() if parts else "help"
        rest = parts[1].strip() if len(parts) > 1 else ""
        if sub == "guard-test":
            v = guard.check(rest, _session_dir())
            return f"Guard: {'ALLOWED' if v.allowed else 'REFUSED'} ({v.reason}) for {str(v.path) if v.path else repr(rest)}. No file was opened."
        if sub == "check":
            d = json.loads(self.check({"folder": rest}))
            if "error" in d: return "Sharewise: " + d["error"]
            lines = [f"Sharewise checked exactly {d['scanned_exactly']}: {d['photos']} photos (check {d['check_id']})."]
            lines += [f"  {k}: {v}" for k, v in d["what_they_reveal"].items()] or ["  nothing sensitive found"]
            return "\n".join(lines + ["Nothing was changed."])
        if sub == "detect-test":
            v = guard.check(str(Path(os.path.expanduser(rest)).parent), _session_dir())
            if not v.allowed: return f"Sharewise: refused ({v.reason})"
            try:
                found = detect.detect(load_upright(Path(os.path.expanduser(rest))), self._data_dir() / "tmp")
            except detect.DetectUnavailable as exc:
                return f"Sharewise: {exc}"
            except Exception as exc:
                return f"Sharewise: could not test ({type(exc).__name__})"
            kinds: dict = {}
            for a in found: kinds[a["why"]] = kinds.get(a["why"], 0) + 1
            return "Sharewise detection test: " + (", ".join(f"{v} {k}" for k, v in kinds.items()) or "nothing found") + ". Nothing was saved."
        if sub == "apply":
            p = parse_code(rest)
            if p is None: return "Sharewise: that selection code is not valid. Copy it again from the review page."
            args = {"check_id": p["check"], "photos": [p["photo"]], "selection_codes": [rest]}
            gate = self.approval_request(args)
            if gate and gate.get("action") == "block": return gate["message"]
            d = json.loads(self.share(args))
            return "Sharewise: " + (d.get("error") or f"{d['copies']} copy made in {d['folder']} ({d['areas_hidden']} areas hidden), verified clean.")
        return ("Sharewise commands (run as typed, no AI):\n"
                "  /sharewise check <folder>        what these photos reveal (yes/no only)\n"
                "  /sharewise apply <code>          make the copy for one photo from its review-page code\n"
                "  /sharewise detect-test <photo>   does automatic detection work on this Mac? (counts only)\n"
                "  /sharewise guard-test <path>     is this path allowed? reads nothing")
