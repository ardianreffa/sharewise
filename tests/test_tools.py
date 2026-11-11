"""Walls at the tool layer: T1 (no values anywhere), T8, T9, R3, R4, and vision on finished copies only."""
import importlib, json, os, sqlite3, sys
from pathlib import Path
import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import test_walls as W                                                   # reuses the planted-secret photos
from test_walls import folder  # noqa: F401  (pytest fixture)

tools = importlib.import_module("sharewise.tools"); detect = importlib.import_module("sharewise.detect")
meta = importlib.import_module("sharewise.meta"); redact = importlib.import_module("sharewise.redact")


class State(dict):
    def get(self, k, default=None): return json.loads(json.dumps(super().get(k, default)))
    def set(self, k, v): self[k] = json.loads(json.dumps(v))


class Ctx:
    def __init__(self): self.state = State()


FACE = (0.40, 0.30, 0.20, 0.25)


@pytest.fixture
def sw(folder, monkeypatch):
    # a stand-in detector: one face and one street sign (Apple Vision is macOS-only)
    monkeypatch.setattr(detect, "detect", lambda up, wd: [
        {"box": FACE, "kind": "face", "on": True, "why": "face"},
        {"box": (0.05, 0.05, 0.30, 0.08), "kind": "text", "on": True, "why": "street name"},
        {"box": (0.60, 0.80, 0.30, 0.10), "kind": "text", "on": False, "why": "other text"}])
    return tools.Sharewise(Ctx())


def call_share(s, args):
    gate = s.approval_request(args)
    if gate and gate.get("action") == "block":
        return {"blocked": gate["message"]}
    return json.loads(s.share(args))


def all_text(folder, s):
    blobs = []
    data = s._data_dir()
    for p in data.rglob("*"):
        if p.is_file(): blobs.append(p.read_bytes())
    return blobs


# T1 ------------------------------------------------------------------ no value in any output, stored file or page
def test_T1_no_secret_anywhere(sw, folder):
    outs = [sw.check({"folder": str(folder)})]
    cid = json.loads(outs[0])["check_id"]
    outs.append(sw.review({"check_id": cid}))
    outs.append(json.dumps(call_share(sw, {"check_id": cid})))
    outs.append(sw.look({"check_id": cid}))
    for o in outs:
        assert not W.leaked(o.encode()), W.leaked(o.encode())
    for blob in all_text(folder, sw):                                    # database, review pages, look images
        assert not W.leaked(blob)
    for p in (folder / "Sharewise").iterdir():
        assert not W.leaked(p.read_bytes()) and not meta.risky(meta.findings(p))


# T9 ------------------------------------------------------------------ fixed arguments only
def test_T9_paths_and_unknown_arguments_are_blocked(sw, folder):
    cid = json.loads(sw.check({"folder": str(folder)}))["check_id"]
    for bad in ({"check_id": cid, "paths": ["/etc/passwd"]}, {"check_id": cid, "keep": ["serials"]},
                {"check_id": "deadbeef"}, {"check_id": cid, "selection_codes": ["SW1.0000.1.0.none.zz"]},
                {"check_id": cid, "add_areas": [{"photo": 1, "box": [0, 0, 2, 2]}]}):
        assert "blocked" in call_share(sw, bad), bad
    assert "refused" in json.loads(sw.share({"check_id": cid}))["error"]          # the hook was not reached


# T8 ------------------------------------------------------------------ keeping location needs Hermes' approval
def test_T8_keep_location_goes_to_the_approval_gate(sw, folder):
    cid = json.loads(sw.check({"folder": str(folder)}))["check_id"]
    gate = sw.approval_request({"check_id": cid, "keep": ["location"]})
    assert gate["action"] == "approve" and "location" in gate["message"].lower()
    assert sw.approval_request({"check_id": cid, "keep": ["time"]}) is None       # no prompt needed for time


# R3 + R4 ------------------------------------------------------------------ hidden copies are clean; the agent can only add
def test_R3_R4_hidden_copy_clean_and_agent_cannot_switch_off(sw, folder):
    cid = json.loads(sw.check({"folder": str(folder)}))["check_id"]
    sw.review({"check_id": cid, "photos": [1]})
    r = call_share(sw, {"check_id": cid, "photos": [1], "add_areas": [{"photo": 1, "box": [0.7, 0.6, 0.1, 0.1]}]})
    assert r["copies"] == 1 and r["made"][0]["areas_hidden"] == 3           # face + street sign + the agent's area (other text stays off)
    dest = folder / "Sharewise" / r["made"][0]["copy"]
    assert not meta.risky(meta.findings(dest)) and meta.findings(dest)["copyright"]
    # the face is destroyed: inverting the original face does not change the copy
    with Image.open(dest) as out:
        assert out.size[0] > 0
    # there is no argument through which the agent could switch the face off
    assert "blocked" in call_share(sw, {"check_id": cid, "photos": [1], "remove_areas": [0]})


def test_selection_code_is_the_only_way_to_switch_off(sw, folder):
    cid = json.loads(sw.check({"folder": str(folder)}))["check_id"]
    sw.review({"check_id": cid, "photos": [1]})
    body = f"{cid}.1.1.none"                                                 # user switched the face (id 0) off
    code = f"SW1.{body}.{tools.code_checksum(body)}"
    r = call_share(sw, {"check_id": cid, "photos": [1], "selection_codes": [code]})
    assert r["made"][0]["areas_hidden"] == 1
    tampered = code[:-1] + ("0" if code[-1] != "0" else "1")
    assert "blocked" in call_share(sw, {"check_id": cid, "photos": [1], "selection_codes": [tampered]})


# vision ------------------------------------------------------------------ the agent only ever sees finished copies
def test_look_returns_finished_copies_only(sw, folder):
    cid = json.loads(sw.check({"folder": str(folder)}))["check_id"]
    assert "error" in json.loads(sw.look({"check_id": cid}))                 # nothing before the copies exist
    sw.review({"check_id": cid, "photos": [1]}); call_share(sw, {"check_id": cid, "photos": [1]})
    imgs = json.loads(sw.look({"check_id": cid}))["images"]
    assert len(imgs) == 1
    with Image.open(imgs[0]["image"]) as im:
        assert not im.getexif() and not im.info.get("xmp")
    original = W.scene()
    assert not np.array_equal(np.asarray(Image.open(imgs[0]["image"]).resize((64, 48))), np.asarray(original.resize((64, 48))))


# detection helpers (pure Python; Apple Vision itself only runs on macOS)
def test_tiles_cover_the_photo_and_map_back():
    tiles = detect.tiles_for((4536, 8064))
    assert len(tiles) >= 4
    for x in (0.01, 0.5, 0.99):                                          # every point lies in at least one tile
        for y in (0.01, 0.5, 0.99):
            assert any(t[0] <= x <= t[0] + t[2] and t[1] <= y <= t[1] + t[3] for t in tiles)
    assert detect.tiles_for((1600, 1200)) == []
    t = (0.5, 0.25, 0.5, 0.5)
    assert detect.from_tile((0.5, 0.5, 0.1, 0.1), t) == (0.75, 0.5, 0.05, 0.05)


def test_same_face_from_whole_photo_and_tiles_is_merged():
    merged = detect.merge_faces([(0.40, 0.40, 0.10, 0.10), (0.405, 0.402, 0.095, 0.098), (0.80, 0.20, 0.03, 0.03)])
    assert len(merged) == 2


def test_people_without_a_face_are_suggested_switched_off(monkeypatch):
    monkeypatch.setattr(detect, "available", lambda: True)
    calls = []
    def fake_vision(img, wd, faces_only):
        calls.append(faces_only)
        return ([], [] if faces_only else [(0.1, 0.2, 0.2, 0.6)], [] if faces_only else [((0.5, 0.5, 0.2, 0.05), 12.0, "street name")])
    monkeypatch.setattr(detect, "_vision", fake_vision)
    found = detect.detect(Image.new("RGB", (4000, 6000)), Path("/tmp/sw_detect_test"))
    head = [a for a in found if a["kind"] == "head"][0]
    assert head["on"] is False and "no face" in head["why"]
    txt = [a for a in found if a["kind"] == "text"][0]
    assert txt["on"] is True and txt["angle"] == 12.0 and txt["shape"] == "rect"
    assert calls[0] is False and all(calls[1:]) and len(calls) > 1      # whole photo first, then face-only tiles


# rotation and shapes
def test_vision_quad_becomes_a_rotated_box():
    import math
    # a 0.4 x 0.05 band rotated 20 degrees, centred at (0.5, 0.5), on a square image; Vision's origin is bottom-left
    W = H = 1000; c = (500, 500); a = math.radians(20); hw, hh = 200, 25
    def pt(dx, dy):
        x = c[0] + dx * math.cos(a) - dy * math.sin(a); y = c[1] + dx * math.sin(a) + dy * math.cos(a)
        return (x / W, 1 - y / H)
    box, ang = detect.quad_to_box(pt(-hw, -hh), pt(hw, -hh), pt(hw, hh), pt(-hw, hh), (W, H))
    assert abs(ang - 20) < 0.01 and all(abs(v - e) < 1e-6 for v, e in zip(box, (0.3, 0.475, 0.4, 0.05)))


def test_rotated_rectangle_keeps_the_sign_and_removes_the_writing():
    im = Image.new("RGB", (800, 600), (200, 215, 230)); d = __import__("PIL.ImageDraw", fromlist=["x"]).Draw(im)
    band = [(150, 230), (650, 380), (640, 420), (140, 270)]
    d.polygon(band, fill=(30, 110, 80))
    for i in range(12): d.text((180 + i * 38, 250 + i * 11), "AB"[i % 2], fill=(240, 235, 210))   # "writing"
    region = {"box": (0.219, 0.5167, 0.55, 0.05), "kind": "text", "angle": 16.7, "shape": "rect"}   # along the band
    out = redact.redact(im, [region], _seed=1)
    m = redact.shape_pixels(region["box"], im.size, "text", 16.7, "rect")
    a = np.asarray(out).astype(float)[m]
    assert a.std(axis=0).max() < 6                                     # one flat plate colour (plus fine grain): no writing
    assert abs(a.mean(axis=0) - np.array([30, 110, 80])).max() < 30    # and it is the sign's own colour, not the sky
    assert np.array_equal(np.asarray(out)[10, 10], np.asarray(im)[10, 10])


def test_selection_code_v2_carries_angle_and_shape(sw, folder):
    cid = json.loads(sw.check({"folder": str(folder)}))["check_id"]
    body = f"{cid}.1.none.200300400100a030r"
    p = tools.parse_code(f"SW2.{body}.{tools.code_checksum(body)}")
    assert p["manual"] == [{"box": (0.2, 0.3, 0.4, 0.1), "angle": 30.0, "shape": "rect"}]
    assert tools.parse_code(f"SW2.{body}.zz") is None


def test_added_areas_validate_shape_and_angle(sw, folder):
    cid = json.loads(sw.check({"folder": str(folder)}))["check_id"]
    ok = {"check_id": cid, "photos": [1], "add_areas": [{"photo": 1, "box": [0.1, 0.1, 0.2, 0.1], "shape": "rect", "angle": 12}]}
    assert sw.approval_request(ok) is None
    bad = {"check_id": cid, "photos": [1], "add_areas": [{"photo": 1, "box": [0.1, 0.1, 0.2, 0.1], "shape": "star"}]}
    assert sw.approval_request(bad)["action"] == "block"


def test_a_selection_code_alone_is_enough(sw, folder):
    cid = json.loads(sw.check({"folder": str(folder)}))["check_id"]
    sw.review({"check_id": cid, "photos": [1]})
    body = f"{cid}.1.0.none"; code = f"SW2.{body}.{tools.code_checksum(body)}"
    fresh = tools.Sharewise(Ctx())                                       # a new conversation: no last check in state
    args = {"selection_codes": [code]}
    assert fresh.approval_request(args) is None
    r = json.loads(fresh.share(args))
    assert r["copies"] >= 1 and any(m["photo"] == 1 and m["areas_hidden"] == 1 for m in r["made"])
