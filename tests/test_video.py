"""Video walls: yes/no only, originals untouched, picture and sound byte-identical, copyright kept, verified."""
import hashlib, importlib, json, os, struct, sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_walls as W                                                   # registers the package
video = importlib.import_module("sharewise.video"); tools = importlib.import_module("sharewise.tools")

SECRETS = [b"+01.2868+103.8545/", b"SpyPhone 9", b"SpyCam", b"2026-11-08T17:42:11"]
MEDIA = os.urandom(50_000)                                               # stands in for compressed picture and sound


def box(t: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + t + payload


def full(t: bytes, ver_flags: bytes, payload: bytes) -> bytes:
    return box(t, ver_flags + payload)


ORPHAN = b"\x00\x00\x00\x30meta" + b"com.apple.quicktime.location.ISO6709" + SECRETS[0]       # a stray header block in the media area
STALE = b"com.apple.quicktime.model" + SECRETS[1] + b"com.apple.quicktime.creationdate" + SECRETS[3]


def make_video(path: Path, orphan: bytes = ORPHAN) -> Path:
    t = 3_845_000_000                                                    # a creation time (seconds since 1904)
    ftyp = box(b"ftyp", b"qt  \x00\x00\x02\x00qt  ")
    media_at = len(ftyp) + 8                                             # where the picture/sound bytes start
    mdat = box(b"mdat", MEDIA + orphan)
    mvhd = full(b"mvhd", b"\x00\x00\x00\x00", struct.pack(">IIII", t, t, 600, 1200) + b"\x00" * 80)
    tkhd = full(b"tkhd", b"\x00\x00\x00\x07", struct.pack(">II", t, t) + b"\x00" * 72)
    mdhd = full(b"mdhd", b"\x00\x00\x00\x00", struct.pack(">IIII", t, t, 600, 1200) + b"\x00" * 4)
    hdlr = full(b"hdlr", b"\x00" * 4, b"\x00" * 4 + b"vide" + b"\x00" * 13)
    stsd = full(b"stsd", b"\x00" * 4, struct.pack(">I", 1) + box(b"avc1", b"\x00" * 78))
    stsz = full(b"stsz", b"\x00" * 4, struct.pack(">III", 0, 1, len(MEDIA)))
    stsc = full(b"stsc", b"\x00" * 4, struct.pack(">IIII", 1, 1, 1, 1))
    stco = full(b"stco", b"\x00" * 4, struct.pack(">II", 1, media_at))
    trak = box(b"trak", tkhd + box(b"mdia", mdhd + hdlr + box(b"minf", box(b"stbl", stsd + stsz + stsc + stco))))
    item = lambda k, v: box(k, struct.pack(">HH", len(v), 0) + v)
    udta = box(b"udta", item(b"\xa9xyz", SECRETS[0]) + item(b"\xa9mod", SECRETS[1]) + item(b"\xa9mak", SECRETS[2]) +
               item(b"\xa9day", SECRETS[3]) + item(b"\xa9cpy", b"(c) 2026 Test Photographer"))
    keys = box(b"keys", b"\x00" * 4 + struct.pack(">I", 1) + box(b"mdta", b"com.apple.quicktime.location.ISO6709"))
    ilst = box(b"ilst", box(b"\x00\x00\x00\x01", box(b"data", b"\x00\x00\x00\x01\x00\x00\x00\x00" + SECRETS[0])))
    meta = box(b"meta", box(b"hdlr", b"\x00" * 8 + b"mdta" + b"\x00" * 13) + keys + ilst)
    moov = box(b"moov", mvhd + trak + udta + meta)
    data = ftyp + mdat + moov + box(b"free", STALE)                      # stale header left in padding
    path.write_bytes(data); return path


def media_bytes(p: Path) -> bytes:
    b = p.read_bytes(); i = b.index(b"mdat")
    return b[i + 4:i + 4 + len(MEDIA)]


@pytest.fixture
def vfolder(tmp_path, monkeypatch):
    h = tmp_path / "home"; d = h / "Movies" / "trip"; d.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h)); monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    make_video(d / "IMG_9001.MOV"); return d


def test_video_findings_are_yes_no(vfolder):
    f = video.findings(vfolder / "IMG_9001.MOV")
    assert all(isinstance(v, bool) for v in f.values())
    assert f["location"] and f["time"] and f["device"] and f["copyright"]
    assert not any(s in repr(f).encode() for s in SECRETS)


def test_video_clean_copy(vfolder):
    src = vfolder / "IMG_9001.MOV"; before = hashlib.sha256(src.read_bytes()).hexdigest()
    dest = video.clean_copy(src, vfolder / "Sharewise")
    blob = dest.read_bytes()
    assert not [s for s in SECRETS if s in blob]                          # no location, model, maker or date left
    assert dest.stat().st_size == src.stat().st_size                     # same size: nothing re-encoded
    assert media_bytes(dest) == media_bytes(src) == MEDIA                # picture and sound byte-identical
    assert b"(c) 2026 Test Photographer" in blob                          # copyright kept (wall T4)
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before        # original untouched
    f = video.findings(dest)
    assert not f["location"] and not f["time"] and not f["device"] and f["copyright"]


def test_video_orphans_and_padding_are_cleaned(vfolder):
    dest = video.clean_copy(vfolder / "IMG_9001.MOV", vfolder / "Sharewise")
    blob = dest.read_bytes()
    assert ORPHAN not in blob and b"quicktime.location" not in blob and STALE not in blob
    assert video.byte_scan(dest) == []


def test_unusual_structure_is_refused(vfolder):
    odd = make_video(vfolder / "odd.mov", orphan=b"\x01" * 20_000)           # 40% of the media area unreferenced
    with pytest.raises(ValueError, match="unusual"):
        video.clean_copy(odd, vfolder / "Sharewise")
    assert not any((vfolder / "Sharewise").glob("odd*"))


def test_video_failed_verification_leaves_nothing(vfolder, monkeypatch):
    monkeypatch.setattr(video, "findings", lambda p: {"location": True, "time": False, "device": False})
    with pytest.raises(ValueError):
        video.clean_copy(vfolder / "IMG_9001.MOV", vfolder / "Sharewise")
    assert not any((vfolder / "Sharewise").glob("*"))


def test_tools_check_and_share_videos(vfolder):
    class State(dict):
        def get(self, k, default=None): return json.loads(json.dumps(super().get(k, default)))
        def set(self, k, v): self[k] = json.loads(json.dumps(v))
    class Ctx:
        state = State()
    s = tools.Sharewise(Ctx())
    c = json.loads(s.check({"folder": str(vfolder)}))
    assert c["videos"] == 1 and "videos_note" in c and not any(x in json.dumps(c).encode() for x in SECRETS)
    args = {"check_id": c["check_id"]}; assert s.approval_request(args) is None
    r = json.loads(s.share(args))
    assert r["copies"] == 1 and r["made"][0]["video"]
    out = vfolder / "Sharewise" / r["made"][0]["copy"]
    assert not [x for x in SECRETS if x in out.read_bytes()]
