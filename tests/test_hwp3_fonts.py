"""글꼴 찾는 순서(hwp3.fonts) 시험: HWP3_FONT_DIR → 패키지 fonts/ → 운영체제 글꼴 폴더.

    pytest tests/test_hwp3_fonts.py

fontTools·brotli가 없으면 글꼴을 실제로 여는 시험 3건은 건너뛴다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hwp3 import fonts as F

SERIF, SANS = "Noto Serif KR", "Noto Sans KR"


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """환경변수·패키지·시스템 글꼴 폴더를 임시 폴더로 바꾼다(실제 설치 글꼴이 섞이지 않게)."""
    d = {k: tmp_path / k for k in ("env", "package", "mac", "linux")}
    for p in d.values():
        p.mkdir()
    monkeypatch.delenv(F.ENV_VAR, raising=False)
    monkeypatch.setattr(F, "PACKAGE_FONT_DIR", d["package"])
    monkeypatch.setattr(F, "system_font_dirs", lambda: [d["mac"], d["linux"], tmp_path / "missing"])
    F.clear_cache()
    yield d
    F.clear_cache()


def touch(folder: Path, *names: str) -> None:
    for n in names:
        p = folder / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
    F.clear_cache()


def test_order_env_then_package_then_system(dirs, monkeypatch):
    assert F.find_font(SERIF) is None and F.find_fonts() == {}
    touch(dirs["linux"], "NotoSerifKR[wght].ttf")
    assert (F.find_font(SERIF).regular.path.parent, F.find_font(SERIF).regular.source) == (dirs["linux"], "system")
    touch(dirs["mac"], "NotoSerifKR[wght].ttf")
    assert F.find_font(SERIF).regular.path.parent == dirs["mac"]  # 시스템 폴더끼리는 적힌 순서
    touch(dirs["package"], "NotoSerifKR[wght].ttf")
    assert (F.find_font(SERIF).regular.path.parent, F.find_font(SERIF).regular.source) == (dirs["package"], "package")
    touch(dirs["env"], "NotoSerifKR-Regular.otf")
    assert F.find_font(SERIF).regular.source == "package"  # 환경변수를 주지 않으면 그 폴더는 보지 않는다
    monkeypatch.setenv(F.ENV_VAR, str(dirs["env"]))
    fs = F.find_font(SERIF)
    assert (fs.regular.path, fs.regular.source) == (dirs["env"] / "NotoSerifKR-Regular.otf", "env")
    assert [s for s, _ in F.font_dirs()] == ["env", "package", "system", "system", "system"]


def test_each_family_falls_back_on_its_own(dirs, monkeypatch):
    touch(dirs["env"], "NotoSansKR[wght].ttf")
    touch(dirs["linux"], "opentype/noto/NotoSerifCJK-Regular.ttc", "opentype/noto/NotoSansCJK-Regular.ttc")
    monkeypatch.setenv(F.ENV_VAR, str(dirs["env"]))
    found = F.find_fonts()
    assert found[SANS].regular.path == dirs["env"] / "NotoSansKR[wght].ttf"
    assert found[SERIF].regular.path == dirs["linux"] / "opentype/noto/NotoSerifCJK-Regular.ttc"  # 하위 폴더까지 본다
    rep = F.report()
    assert rep["found"][SERIF]["source"] == "system" and rep["found"][SANS]["source"] == "env"
    assert [d["exists"] for d in rep["search_order"]] == [True, True, True, True, False]


def test_env_takes_several_dirs(dirs, monkeypatch):
    import os

    touch(dirs["linux"], "NotoSerifKR-Regular.otf")
    monkeypatch.setenv(F.ENV_VAR, os.pathsep.join([str(dirs["env"]), str(dirs["linux"])]))
    assert F.find_font(SERIF).regular.source == "env"
    assert [str(d) for s, d in F.font_dirs() if s == "env"] == [str(dirs["env"]), str(dirs["linux"])]


def test_file_name_choice(dirs):
    touch(dirs["mac"], "NotoSansKR-Bold.otf", "NotoSansKR-Light.otf", "NotoSansJP-Regular.otf", "NotoSansCJKjp-Regular.otf", "NotoSansMono-Regular.ttf", "notes.txt")
    assert F.find_font(SANS) is None  # 굵은·가는 파일, 다른 언어, 다른 가족은 보통 굵기로 쓰지 않는다
    touch(dirs["mac"], "NotoSansCJK-Regular.ttc")
    assert F.find_font(SANS).regular.path.name == "NotoSansCJK-Regular.ttc"
    touch(dirs["mac"], "NotoSansCJKkr-Regular.otf")
    assert F.find_font(SANS).regular.path.name == "NotoSansCJKkr-Regular.otf"
    touch(dirs["mac"], "NotoSansKR-Regular.otf")
    fs = F.find_font(SANS)
    assert fs.regular.path.name == "NotoSansKR-Regular.otf" and not fs.regular.variable
    assert fs.bold.path.name == "NotoSansKR-Bold.otf"  # 가변 글꼴이 아니면 같은 계열의 굵은 파일을 함께 쓴다
    touch(dirs["mac"], "NotoSansKR-VF.ttf")
    fs = F.find_font(SANS)
    assert fs.regular.path.name == "NotoSansKR-VF.ttf" and fs.regular.variable and fs.bold is None
    touch(dirs["mac"], "NotoSansKR[wght].ttf")
    assert F.find_font(SANS).regular.path.name == "NotoSansKR-VF.ttf"  # 같은 등급이면 이름 순서로 고정
    assert F.find_font(SERIF) is None
    with pytest.raises(KeyError):
        F.find_font("Arial")


def test_default_dirs_cover_mac_and_linux(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    got = [str(d) for d in F.system_font_dirs()]
    for want in (f"{tmp_path}/Library/Fonts", "/Library/Fonts", "/System/Library/Fonts",
                 f"{tmp_path}/.local/share/fonts", f"{tmp_path}/.fonts", "/usr/local/share/fonts", "/usr/share/fonts"):
        assert want in got, want
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert str(tmp_path / "xdg" / "fonts") in [str(d) for d in F.system_font_dirs()]
    assert F.PACKAGE_FONT_DIR == Path(F.__file__).resolve().parent / "fonts"


# ---------------- 글꼴을 실제로 여는 시험 ----------------

def _tiny_font(family: str, style: str = "Regular", weight: int = 400):
    """'가'·'A' 두 글자만 든 글꼴."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    fb = FontBuilder(1000, isTTF=True)
    order = [".notdef", "A", "uniAC00"]
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({0x41: "A", 0xAC00: "uniAC00"})
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    for xy in ((0, 700), (500, 700), (500, 0)):
        pen.lineTo(xy)
    pen.closePath()
    g = pen.glyph()
    fb.setupGlyf({n: g for n in order})
    fb.setupHorizontalMetrics({n: (600, 0) for n in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": family, "styleName": style})
    fb.setupOS2(usWeightClass=weight)
    fb.setupPost()
    return fb.font


def _faces(css: str) -> list[tuple[str, str]]:
    import re

    return re.findall(r"font-family:'([^']+)';src:url\(data:font/woff2;base64,[^)]+\) format\('woff2'\);font-weight:([^}]+)\}", css)


def test_static_fonts_embed_regular_and_bold(dirs, monkeypatch):
    pytest.importorskip("fontTools")
    pytest.importorskip("brotli")
    from hwp3.render import _font_faces

    _tiny_font("Noto Sans KR").save(dirs["env"] / "NotoSansKR-Regular.ttf")
    _tiny_font("Noto Sans KR", "Bold", 700).save(dirs["env"] / "NotoSansKR-Bold.ttf")
    _tiny_font("Noto Serif KR").save(dirs["env"] / "NotoSerifKR-Regular.ttf")
    monkeypatch.setenv(F.ENV_VAR, str(dirs["env"]))
    F.clear_cache()
    assert _faces(_font_faces({"가", "A"})) == [(SERIF, "400"), (SANS, "400"), (SANS, "700")]


def test_ttc_picks_korean_face(dirs, monkeypatch):
    pytest.importorskip("fontTools")
    from fontTools.ttLib import TTCollection

    coll = TTCollection()
    coll.fonts = [_tiny_font("Noto Sans CJK JP"), _tiny_font("Noto Sans CJK KR"), _tiny_font("Noto Sans CJK SC")]
    coll.save(dirs["linux"] / "NotoSansCJK-Regular.ttc")
    F.clear_cache()
    fs = F.find_font(SANS)
    assert fs.regular.path.name == "NotoSansCJK-Regular.ttc"
    assert F.open_font(fs.regular.path)["name"].getDebugName(1) == "Noto Sans CJK KR"


def test_pages_embed_fonts_from_env_dir(dirs, monkeypatch, tmp_path):
    """쪽 보기(write_pages)가 HWP3_FONT_DIR의 글꼴을 넣는다. 글꼴이 없으면 넣지 않고도 쪽은 만든다."""
    pytest.importorskip("fontTools")
    pytest.importorskip("brotli")
    from hwp3 import parse_bytes
    from hwp3.render import write_pages
    from test_hwp3 import sample_doc

    doc = parse_bytes(sample_doc())
    r = write_pages(doc, tmp_path / "none")
    assert r["fonts_embedded"] is False and r["pages"] >= 1
    _tiny_font("Noto Serif KR").save(dirs["env"] / "NotoSerifKR-Regular.ttf")
    monkeypatch.setenv(F.ENV_VAR, str(dirs["env"]))
    F.clear_cache()
    r = write_pages(doc, tmp_path / "env")
    assert r["fonts_embedded"] is True
    assert _faces((tmp_path / "env" / "pages" / "index.html").read_text(encoding="utf-8")) == [(SERIF, "400")]
