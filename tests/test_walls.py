"""The safety walls, tested before any feature. Every secret below must never come out of Sharewise."""
import hashlib, importlib, importlib.util, io, os, re, shutil, sys
from pathlib import Path
import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sharewise", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
mod = importlib.util.module_from_spec(spec); sys.modules["sharewise"] = mod; spec.loader.exec_module(mod)
meta = importlib.import_module("sharewise.meta"); clean = importlib.import_module("sharewise.clean"); guard = importlib.import_module("sharewise.guard")

SECRETS = ["SPY-SERIAL-7781", "Ardian Secret Owner", "Jalan Mawar 12", "SpyCam", "SpyPhone 9", "2026:11:08 17:42:11",
           "LAT-7.5683", "HIDDEN-MOTION-VIDEO"]
COPYRIGHT = "(c) 2026 Test Photographer"
REAL_HEIC = Path("/mnt/user-data/uploads/IMG_7296.HEIC")


def scene():
    im = Image.new("RGB", (640, 480), (180, 170, 150)); d = ImageDraw.Draw(im)
    for i in range(40): d.rectangle((i * 16, (i * 37) % 480, i * 16 + 30, (i * 37) % 480 + 25), fill=((i * 50) % 255, 90, (i * 20) % 255))
    return im


def secret_jpeg(path: Path):
    ex = Image.Exif()
    ex[271] = "SpyCam"; ex[272] = "SpyPhone 9"; ex[274] = 6; ex[315] = "Ardian Secret Owner"; ex[33432] = COPYRIGHT
    ex[270] = "Jalan Mawar 12"
    sub = ex.get_ifd(0x8769); sub[36867] = "2026:11:08 17:42:11"; sub[42033] = "SPY-SERIAL-7781"; sub[37510] = b"ASCII\x00\x00\x00Jalan Mawar 12"
    gps = ex.get_ifd(0x8825); gps.update({1: "S", 2: (7.0, 34.0, 5.88), 3: "E", 4: (110.0, 49.0, 30.0), 6: 120.5, 17: 271.3})
    xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           b'<rdf:Description xmlns:exif="http://ns.adobe.com/exif/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
           b'exif:GPSLatitude="7,34.098S" exif:GPSLongitude="110,49.5E"><dc:creator><rdf:Seq><rdf:li>Ardian Secret Owner</rdf:li></rdf:Seq></dc:creator>'
           b'<dc:rights><rdf:Alt><rdf:li xml:lang="x-default">' + COPYRIGHT.encode() + b'</rdf:li></rdf:Alt></dc:rights></rdf:Description></rdf:RDF></x:xmpmeta>')
    buf = io.BytesIO(); scene().save(buf, "JPEG", quality=90, exif=ex.tobytes(), comment=b"Jalan Mawar 12", xmp=xmp)
    path.write_bytes(buf.getvalue() + b"\x00\x00\x00\x18ftypmp42HIDDEN-MOTION-VIDEO LAT-7.5683")   # Motion Photo style trailer
    return path


def secret_png(path: Path):
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo(); info.add_text("Author", "Ardian Secret Owner"); info.add_text("GPS", "LAT-7.5683"); info.add_text("Comment", "Jalan Mawar 12")
    info.add_text("Copyright", COPYRIGHT); scene().save(path, "PNG", pnginfo=info); return path


def leaked(blob: bytes):
    return [s for s in SECRETS if s.encode() in blob]


@pytest.fixture
def folder(tmp_path, monkeypatch):
    h = tmp_path / "home"; d = h / "Pictures" / "share"; d.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h)); monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    secret_jpeg(d / "IMG_0001.JPG"); secret_png(d / "shot.png")
    if REAL_HEIC.exists(): shutil.copy(REAL_HEIC, d / "IMG_7296.HEIC")
    return d


# T1 ------------------------------------------------------------------ nothing but yes/no comes out
def test_T1_findings_are_only_booleans(folder):
    for p in folder.iterdir():
        f = meta.findings(p)
        assert all(isinstance(v, bool) for v in f.values()), p.name
        assert not leaked(repr(f).encode())
    f = meta.findings(folder / "IMG_0001.JPG")
    for c in ("location", "location_detail", "time", "device", "serials", "owner", "comments", "copyright"):
        assert f[c], c


@pytest.mark.skipif(not REAL_HEIC.exists(), reason="real iPhone photo not available")
def test_T1_real_iphone_heic_is_detected(folder):
    f = meta.findings(folder / "IMG_7296.HEIC")
    assert f["location"] and f["location_detail"] and f["time"] and f["device"] and f["depth_map"] is not None


# T2 + T4 ------------------------------------------------------------------ remove-only, copyright kept
def test_T2_T4_clean_jpeg_keeps_only_orientation_and_copyright(folder):
    dest = clean.clean_copy(folder / "IMG_0001.JPG", folder / "Sharewise")
    blob = dest.read_bytes()
    assert not leaked(blob), leaked(blob)
    with Image.open(dest) as im:
        ex = im.getexif()
        assert set(ex.keys()) <= {274, 33432} and ex[274] == 6 and ex[33432] == COPYRIGHT
        assert not ex.get_ifd(0x8825) and not im.info.get("xmp")
    f = meta.findings(dest)
    assert not meta.risky(f) and f["copyright"]


def test_T2_png_and_keep_choices(folder):
    dest = clean.clean_copy(folder / "shot.png", folder / "Sharewise")
    assert not leaked(dest.read_bytes()) and meta.findings(dest)["copyright"] and not meta.risky(meta.findings(dest))
    kept = clean.clean_copy(folder / "IMG_0001.JPG", folder / "Sharewise", keep={"time"})
    f = meta.findings(kept)
    assert f["time"] and not f["location"] and not f["serials"] and not f["owner"] and not f["device"]


# T3 ------------------------------------------------------------------ originals untouched, nothing overwritten, lossless JPEG
def test_T3_original_untouched_and_no_overwrite(folder):
    src = folder / "IMG_0001.JPG"; before = hashlib.sha256(src.read_bytes()).hexdigest(); mtime = src.stat().st_mtime
    out = folder / "Sharewise"; out.mkdir(); (out / "IMG_0001-clean.jpg").write_bytes(b"user file")
    dest = clean.clean_copy(src, out)
    assert dest.name == "IMG_0001-clean (2).jpg" and (out / "IMG_0001-clean.jpg").read_bytes() == b"user file"
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before and src.stat().st_mtime == mtime
    with Image.open(src) as a, Image.open(dest) as b:
        assert a.tobytes() == b.tobytes()                       # JPEG cleaning is lossless: identical pixels


# T7 ------------------------------------------------------------------ a copy that fails verification is removed
def test_T7_failed_verification_leaves_nothing(folder, monkeypatch):
    real = meta.findings
    monkeypatch.setattr(meta, "findings", lambda p: {**real(p), "location": True})
    with pytest.raises(clean.CleanError):
        clean.clean_copy(folder / "IMG_0001.JPG", folder / "Sharewise")
    assert not any((folder / "Sharewise").glob("*"))


@pytest.mark.skipif(not REAL_HEIC.exists(), reason="real iPhone photo not available")
def test_T7_real_heic_becomes_clean_jpeg(folder):
    dest = clean.clean_copy(folder / "IMG_7296.HEIC", folder / "Sharewise")
    assert dest.suffix == ".jpg" and not meta.risky(meta.findings(dest))


# T5 ------------------------------------------------------------------ the guard
def test_T5_guard_refuses_protected_places(folder):
    home = Path(os.environ["HOME"]); (home / "Library").mkdir(); (home / ".ssh").mkdir()
    for p in ("/", "/etc", str(home), str(home / "Library"), str(home / ".ssh")):
        assert not guard.check(p, home).allowed, p
    assert guard.check(str(folder), home).allowed


# T6 ------------------------------------------------------------------ no network, shell or global settings in the code
def test_T6_no_network_shell_or_global_settings():
    bad = re.compile(r"import (socket|requests|httpx|urllib\.request|http\.client|subprocess)|from (socket|requests|httpx|http|subprocess)|"
                     r"os\.system|os\.popen|Popen|urlopen|webbrowser|MAX_IMAGE_PIXELS|register_heif_opener|warnings\.filterwarnings")
    for f in ROOT.glob("*.py"):
        assert not bad.search(f.read_text()), f.name


# orientation: a photo is turned upright exactly once (a portrait HEIC once came out sideways)
@pytest.mark.skipif(not REAL_HEIC.exists(), reason="real iPhone photo not available")
def test_heic_is_turned_upright_exactly_once(folder):
    import pillow_heif
    from PIL import ImageOps
    tools = importlib.import_module("sharewise.tools")
    expected = ImageOps.exif_transpose(pillow_heif.open_heif(str(REAL_HEIC)).to_pillow()).size
    assert tools.load_upright(folder / "IMG_7296.HEIC").size == expected
    dest = clean.clean_copy(folder / "IMG_7296.HEIC", folder / "Sharewise")
    with Image.open(dest) as im:
        assert im.size == expected and im.getexif().get(274, 1) == 1


def test_jpeg_orientation_applied_once(folder):
    tools = importlib.import_module("sharewise.tools")
    up = tools.load_upright(folder / "IMG_0001.JPG")                 # stored 640x480 with orientation 6 (rotate 90)
    assert up.size == (480, 640)
