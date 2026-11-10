"""HWP3 파서 시험.

1) 합성 문서: 규격(한글 문서 파일 형식 3.0 rev1.2) 표대로 바이트를 직접 조립해 해석 결과를 확인한다.
2) 글자 코드: 규격 예시(개요 불릿 ○ = 0x343b)와 계산식 범위.

    pytest tests/test_hwp3.py
"""

from __future__ import annotations

import struct
import zlib

import pytest
from hwp3 import HWP3Error, Table, decode_hchar, parse_bytes
from hwp3.export import render_text, to_json

SIG = b"HWP Document File V3.00 \x1a\x01\x02\x03\x04\x05"


# ---------------- 합성 문서 작성기 ----------------

def hstr(s: str) -> list[int]:
    out = []
    for ch in s:
        if ord(ch) < 0x80:
            out.append(ord(ch))
        else:
            b = ch.encode("johab")
            out.append((b[0] << 8) | b[1])
    return out


def para(chars: list[bytes | int], nline: int = 1, line_flag: int = 0, etc: int = 0) -> bytes:
    """chars: 일반 글자는 int(hchar), 특수 문자는 미리 만든 bytes. 끝에 CR(13)을 붙인다."""
    from hwp3.parser import WSIZE

    body = b""
    nch = 0
    for c in chars:
        if isinstance(c, int):
            body += struct.pack("<H", c)
            nch += 1
        else:
            code = struct.unpack_from("<H", c, 0)[0]
            body += c
            nch += WSIZE[code]
    body += struct.pack("<H", 13)
    nch += 1
    head = struct.pack("<BHHBBIB", 0, nch, nline, 0, etc, 0, 0) + bytes(31) + bytes(187)
    lines = b"".join(struct.pack("<HHH6sH", 0, 0, 0, bytes(6), line_flag) for _ in range(nline))
    return head + lines + body


END_PARA = struct.pack("<BHHBBIB", 0, 0, 0, 0, 0, 0, 0) + bytes(31)


def plist(*paras: bytes) -> bytes:
    return b"".join(paras) + END_PARA


def tab() -> bytes:
    return struct.pack("<HHHH", 9, 400, 0, 9)


def table(cells: list[tuple[int, int, int, int, str]]) -> bytes:
    """cells: (x, y, w, h, 글자)."""
    info = bytes(8) + struct.pack("<BBhhHH", 0, 0, 0, 0, 0, 10) + bytes(24)
    w = max(x + cw for x, _, cw, _, _ in cells)
    h = max(y + ch for _, y, _, ch, _ in cells)
    info += struct.pack("<HH", w, h) + bytes(6) + struct.pack("<HH", w, h) + bytes(4) + b"\x00\x00" + bytes(4) + bytes(4)
    info += struct.pack("<HH", 0, 0) + bytes(2) + struct.pack("<HHHH", 0, 0, len(cells), 0)
    assert len(info) == 84
    cinfo = b""
    for i, (x, y, cw, chh, _) in enumerate(cells):
        cinfo += struct.pack("<BBHHHHHHH", 0, i, 0, x, y, cw, chh, 0, 0) + bytes(2) + bytes([1, 0]) + bytes([1, 1, 1, 1]) + bytes(3)
    lists = b"".join(plist(para(hstr(t))) for *_, t in cells)
    caption = plist()
    return struct.pack("<HIH", 10, 0, 10) + info + cinfo + lists + caption


def footnote(text: str) -> bytes:
    return struct.pack("<HIH", 17, 0, 17) + bytes(8) + struct.pack("<HHH", 0, 0, 1000) + plist(para(hstr(text)))


def build(paragraphs: bytes, compressed: bool = True, preview: str | None = "가나") -> bytes:
    docinfo = bytearray(128)
    docinfo[124] = 1 if compressed else 0
    summary = bytearray(1008)
    title = hstr("합성")
    struct.pack_into(f"<{len(title)}H", summary, 0, *title)
    fonts = b"".join(struct.pack("<H", 1) + b"Font".ljust(40, b"\0") for _ in range(7))
    styles = struct.pack("<H", 1) + b"Normal".ljust(20, b"\0") + bytes(31) + bytes(187)
    body = fonts + styles + paragraphs + bytes(8)  # 추가 정보 블록 끝(tag 0, 길이 0)
    tail = b""
    if preview is not None:
        pv = b"".join(struct.pack("<H", c) for c in hstr(preview))
        tail += struct.pack("<II", 0x80000002, len(pv)) + pv
    tail += struct.pack("<II", 0x80000000, 0)
    if compressed:
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        stream = co.compress(body) + co.flush() + struct.pack("<II", zlib.crc32(body), len(body))
    else:
        stream = body
    return SIG + bytes(docinfo) + bytes(summary) + stream + tail


def drawing_picture(text: str) -> bytes:
    """그림 종류 3(그리기 개체): 틀 헤더 + 글상자 개체 1개(세부 정보 2 = 문단 리스트)."""
    pl = plist(para(hstr(text)))
    obj = struct.pack("<IHH", 88, 6, 0) + bytes(24 + 16) + bytes(44) + struct.pack("<II", 0, len(pl)) + pl
    follow = struct.pack("<III", 24, 0, 1) + bytes(16) + obj
    info = struct.pack("<I", len(follow)) + bytes(4) + struct.pack("<BBhhHH", 0, 0, 0, 0, 0, 11) + bytes(24)
    info += bytes(4 + 6 + 4 + 4) + b"\x00\x00" + bytes(4 + 4) + struct.pack("<HH", 0, 0) + bytes([3]) + bytes(8)
    info += bytes(256) + bytes(4) + bytes(5)
    assert len(info) == 348
    return struct.pack("<HIH", 11, 0, 11) + info + follow + plist()


def sample_doc(compressed: bool = True) -> bytes:
    p1 = para(hstr("가나 AB") + [tab(), 0x36E7, 0x343B], line_flag=0)
    p2 = para([table([(0, 0, 100, 50, "갑"), (100, 0, 100, 50, "을"), (0, 50, 200, 50, "병합")])], line_flag=0x8001)
    p3 = para(hstr("본문") + [footnote("주석")], line_flag=0)
    return build(plist(p1, p2, p3), compressed=compressed)


@pytest.mark.parametrize("compressed", [True, False])
def test_synthetic_document(compressed):
    doc = parse_bytes(sample_doc(compressed))
    assert doc.warnings == []
    assert doc.summary["title"] == "합성"
    assert doc.body_crc_ok is (True if compressed else None)
    assert [p.text() for p in doc.paragraphs] == ["가나 AB\t①○", "", "본문"]
    t = next(doc.iter_tables())
    assert isinstance(t, Table) and t.box_type == "table"
    assert (t.n_rows, t.n_cols) == (2, 2)
    assert t.grid_problems() == []
    spans = {c.text(): (c.row, c.col, c.rowspan, c.colspan) for c in t.cells}
    assert spans == {"갑": (0, 0, 1, 1), "을": (0, 1, 1, 1), "병합": (1, 0, 1, 2)}
    fn = next(iter(doc.paragraphs[2].controls()))
    assert fn.name == "footnote" and fn.children[0][0].text() == "주석"
    # 줄 정보 bit15+bit0: 둘째 문단부터 2쪽
    assert [p.page for p in doc.paragraphs] == [1, 2, 2]
    assert [t["name"] for t in doc.tail_tags] == ["preview_text", "end"]
    txt = render_text(doc)
    assert "[표 T0 2x2" in txt and "갑\t을" in txt and "[각주: 주석]" in txt
    j = to_json(doc)
    assert j["stats"]["controls"]["box:table"] == 1


def test_drawing_textbox_text():
    doc = parse_bytes(build(plist(para(hstr("가") + [drawing_picture("도형 안 글")]))))
    assert doc.warnings == []
    pic = next(doc.paragraphs[0].controls())
    assert pic.fields["pictype"] == 3 and pic.fields["drawing"]["consumed_exactly"] is True
    assert pic.fields["drawing"]["types"] == {6: 1}
    assert [q.text() for lst in pic.children[1:] for q in lst] == ["도형 안 글"]
    assert "[그림 그리기: 도형 안 글]" in render_text(doc)


def test_truncated_file_raises():
    data = sample_doc()
    head = len(SIG) + 128 + 1008
    with pytest.raises(HWP3Error):
        parse_bytes(data[: head + (len(data) - head) // 2])


def test_grid_ignores_one_unit_end_offsets():
    """셀 끝이 이웃 셀 시작과 1단위 어긋나도 폭 1짜리 가짜 열·행을 만들지 않는다(한컴 변환본 rowCnt/colCnt와 같은 규칙)."""
    t = table([
        (0, 0, 1001, 500, "가"), (1000, 0, 1000, 500, "나"),   # 첫 셀 끝 1001 ≠ 둘째 셀 시작 1000
        (0, 500, 2000, 501, "다"),                              # 아래 끝 1001 ≠ 표 아래 끝 1000
        (0, 1000, 1000, 500, "라"), (1000, 1000, 1000, 500, "마"),
    ])
    doc = parse_bytes(build(plist(para([t]))))
    tb = next(doc.iter_tables())
    assert (tb.n_rows, tb.n_cols) == (3, 2)
    assert [(c.row, c.col, c.rowspan, c.colspan) for c in tb.cells] == [(0, 0, 1, 1), (0, 1, 1, 1), (1, 0, 1, 2), (2, 0, 1, 1), (2, 1, 1, 1)]
    assert tb.grid_problems() == []


def test_corrupt_deflate_raises_hwp3error():
    """압축 본문이 깨지면 zlib.error가 아니라 HWP3Error로 거부해야 한다(공개 퍼징 파일 header-in-header.hwp 사례)."""
    data = bytearray(sample_doc())
    data[len(SIG) + 128 + 1008] = 0xFF  # deflate 첫 블록 형식을 예약값(11)으로
    with pytest.raises(HWP3Error):
        parse_bytes(bytes(data))


def test_not_hwp3():
    with pytest.raises(HWP3Error):
        parse_bytes(b"\xd0\xcf\x11\xe0" + bytes(2000))


def test_bad_box_code_raises():
    bad = table([(0, 0, 10, 10, "x"), (10, 0, 10, 10, "y")])
    bad = bad[:8 + 16] + struct.pack("<H", 11) + bad[8 + 18:]  # 표 정보 오프셋 16의 코드 칸을 11로
    with pytest.raises(HWP3Error):
        parse_bytes(build(plist(para([bad]))))


@pytest.mark.parametrize("nch", [3, 6])
def test_char_count_corruption_is_never_silent(nch):
    """글자 수(nch)가 실제와 다르면 경고가 남거나 해석이 실패해야 한다(조용히 성공하면 안 됨)."""
    p = bytearray(para(hstr("가나다")))  # 실제 nch = 4
    struct.pack_into("<H", p, 1, nch)
    try:
        doc = parse_bytes(build(plist(bytes(p))))
    except HWP3Error:
        return
    assert doc.warnings


# ---------------- 글자 코드 ----------------

@pytest.mark.parametrize(
    "code,ch",
    [
        (0x343B, "○"),  # 규격서 10.21 예시
        (0x36E7, "①"),
        (0x3404, "·"),
        (0x340D, "∼"),
        (0x3590, "Ⅰ"),
        (0x34CB, "＋"),
        (0x8861, "가"),
        (0xD3BD, "힣"),  # 조합형 마지막 음절
        (0x301C, "─"),
        (0x301D, "│"),
        (0x3013, "┌"),
        (0x4000, "伽"),  # KS X 1001 한자 첫 글자
        (0x81, "“"),
        (0x41, "A"),
        # 한컴 HNC 표 보충(hnc_table.tsv): 규칙이 모르던 코드. 괄호는 외부 시료에서 실제로 나온 문서.
        (0x00FC, "ü"),  # 라틴1 그대로(german-legislative-system)
        (0x5318, "仮"),  # 2수준 한자 첫 글자(java-hwp 4e00-62ff)
        (0x2E01, "①"),  # hwp3-sample11, 한컴 HWPX 변환본과 같음
        (0x2C21, "ⓐ"),
        (0x3062, "“"),  # SO-SUEOP
        (0x3067, "‘"),
        (0x37ED, "═"),  # hwp3-sample10·11
        (0x205A, "○"),
        (0x3151, "‧"),  # 노임 1999H1·1999H2·2000H2
        (0x87C1, "ᆞ"),  # 아래아(hwp3-sample16)
        (0xD941, "ᄡᅦ"),  # 옛한글: 첫가끝 자모열
        (0x2F11, "◦"),  # 글머리표는 한컴 표가 계산식(○)보다 먼저
        # 한컴 표 값이 한컴 사설 영역인 코드: KTUG 옛한글 표, rhwp 한컴 기호 표, 한컴 PDF 그림 확인
        (0x8040, "ᄞᅠᇙ"),  # 옛한글(KTUG: U+F7AF U+F806 U+F8A7 → 첫가끝 자모)
        (0x3366, "□"),  # hwp3-sample16 글머리표(rhwp 표, 한컴 PDF 대조)
        (0x2BCE, "(인)"),  # 도장 기호(rhwp 표)
        (0x2E00, "⓪"),  # hwp3-sample11 원문자 변형(rhwp 표)
        (0x30BB, "『"),  # SO-SUEOP 한컴 2024 PDF p25 그림
        (0x30BD, "』"),
    ],
)
def test_decode_hchar(code, ch):
    assert decode_hchar(code) == ch


@pytest.mark.parametrize("code", [0x3073, 0x3078, 0x25F1])
def test_unknown_code_returns_none(code):
    """한컴 표에서도 사설 영역 글자뿐인 코드는 모름으로 남긴다(노임 1999H1 등에 실제로 나옴)."""
    assert decode_hchar(code) is None


def test_without_hnc_table_restores_rules_only():
    from hwp3.charset import without_hnc_table

    with without_hnc_table():
        assert decode_hchar(0x3151) is None
        assert decode_hchar(0x2F11) == "○"  # 글머리표 계산식(LibreOffice 규칙)
    assert decode_hchar(0x3151) == "‧"
    assert decode_hchar(0x2F11) == "◦"


def test_johab_codec_not_used_above_d400():
    """0xD400 이상은 HNC에서 옛한글 자리다. KSSM 조합형 기호(0xD932 → '、')로 풀면 안 된다."""
    assert decode_hchar(0xD932) != "、"
    assert decode_hchar(0xE031) not in (None, "伽")  # KSSM 한자 첫 자리 → 한컴 표에서는 옛한글


def test_modern_syllables_all_decoded():
    from hwp3.charset import _rules

    n = 0
    for code in range(0x8000, 0xD400):
        cho, jung, jong = (code >> 10) & 31, (code >> 5) & 31, code & 31
        if 2 <= cho <= 20 and jung in {3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 15, 18, 19, 20, 21, 22, 23, 26, 27, 28, 29} \
                and jong in set(range(1, 18)) | set(range(19, 30)):
            s = decode_hchar(code)
            assert s == _rules(code) and len(s) == 1 and "가" <= s <= "힣"
            n += 1
    assert n == 11172


# ---------------- 괘선 글자 표 ----------------

def _row(cells, widths, left="│", sep="│", right="│"):
    from hwp3.texttable import char_width

    out = []
    for c, w in zip(cells, widths):
        pad = w - sum(char_width(ch) for ch in c)
        out.append(" " * pad + c)
    return left + sep.join(out) + right


def test_two_page_text_table():
    """두 쪽에 걸친 괘선 표: 왼쪽 쪽(┌…└)과 오른쪽 쪽(┬…┘, 번호가 오른쪽 끝)을 각각 표로 읽는다."""
    from hwp3.texttable import find_text_tables

    lw = [4, 8, 10, 10]
    rw = [10, 10, 8, 4]

    def rule(widths, a, b, c):
        return a + b.join("─" * w for w in widths) + c

    lines = [
        rule(lw, "┌", "┬", "┬"),
        _row(["번호", "직종", "97년 9월", "97년 5월"], lw),
        rule(lw, "├", "┼", "┼"),
        _row(["1", "갱부", "56,352", "62,028"], lw),
        _row(["2", "목수", "81,068", "80,541"], lw),
        rule(lw, "└", "┴", "┴"),
        rule(rw, "┬", "┬", "┐"),
        _row(["94년 9월", "94년 5월", "직종", "번호"], rw),
        rule(rw, "┼", "┼", "┤"),
        _row(["29,977", "-", "갱부", "1"], rw),
        _row(["73,539", "57,836", "목수", "2"], rw),
        rule(rw, "┴", "┴", "┘"),
        rule(lw, "┌", "┬", "┬"),
        _row(["번호", "직종", "97년 9월", "97년 5월"], lw),
        _row(["3", "철공", "72,430", "73,156"], lw),
        rule(lw, "└", "┴", "┴"),
    ]
    tts = find_text_tables(lines)
    assert [(t.start, t.end) for t in tts] == [(0, 5), (6, 11), (12, 15)]


# ---------------- 한컴식 보기(hwp3.compat) ----------------

def _box(cells, btype=0, option=0, cap_pos=0):
    """table()로 만든 상자에서 표 정보의 종류(오프셋 78)·기타 옵션(14)·캡션 위치(70)를 바꾼다."""
    b = bytearray(table(cells))
    struct.pack_into("<H", b, 8 + 14, option)
    struct.pack_into("<H", b, 8 + 70, cap_pos)
    struct.pack_into("<H", b, 8 + 78, btype)
    return bytes(b)


def test_compat_textbox_and_button_become_1x1_tables():
    from hwp3 import compat

    doc = parse_bytes(build(plist(para([_box([(0, 0, 100, 50, "글상자")], btype=1)]),
                                  para([_box([(0, 0, 100, 50, "버튼")], btype=3)]),
                                  para([_box([(0, 0, 100, 50, "")], btype=3, option=1 << 4)]),  # 하이퍼텍스트
                                  para([_box([(0, 0, 100, 50, "x+y")], btype=2)]))))  # 수식
    views = list(compat.table_views(doc))
    assert [(v.kind, v.rows, v.cols, v.cells[0].text()) for v in views] == [("textbox", 1, 1, "글상자"), ("button", 1, 1, "버튼")]
    assert compat.equation_scripts(doc) == ["x+y"]
    assert "x+y" not in compat.text(doc)


def test_compat_fills_holes_and_narrow_gaps():
    from hwp3 import compat

    # 열 경계 0·1000·1001·2000: 둘째 줄 셀이 1001에서 시작해 폭 1짜리 열이 생긴다(hwp3-sample16 사례).
    # 그 열의 빈칸은 왼쪽 셀을 늘려 메우고, 셋째 줄 오른쪽 큰 빈칸은 빈 셀로 채운다(hwp3-table-grid-gap 사례).
    t = _box([(0, 0, 1000, 500, "가"), (1000, 0, 1000, 500, "나"),
              (0, 500, 1000, 500, "다"), (1001, 500, 999, 500, "라"),
              (0, 1000, 1000, 500, "마")])
    doc = parse_bytes(build(plist(para([t]))))
    tb = next(doc.iter_tables())
    assert (tb.n_rows, tb.n_cols) == (3, 3)
    assert tb.grid_problems()  # 원본 보기는 빈칸을 알린다
    v = compat.table_view(tb)
    got = sorted((g.row, g.col, g.rowspan, g.colspan, g.origin, g.text()) for g in v.cells)
    assert got == [(0, 0, 1, 1, "cell", "가"), (0, 1, 1, 2, "cell", "나"),
                   (1, 0, 1, 2, "extended", "다"), (1, 2, 1, 1, "cell", "라"),
                   (2, 0, 1, 2, "extended", "마"), (2, 2, 1, 1, "filled", "")]


def test_compat_caption_before_when_top():
    from hwp3 import compat
    from hwp3.model import Paragraph

    doc = parse_bytes(build(plist(para([_box([(0, 0, 100, 50, "셀")], cap_pos=1)]))))
    tb = next(doc.iter_tables())
    tb.caption = [Paragraph(0, 2, 0, 0, 0, 0, 0, b"", None, [], ["제목"])]
    assert compat.text(doc).split("\n")[1:] == ["제목", "셀"]
    tb.fields["cap_pos"] = 0
    assert compat.text(doc).split("\n")[1:] == ["셀", "제목"]


def test_compat_strips_footnote_number_suffix():
    from hwp3 import compat
    from hwp3.model import Control, Paragraph

    p = Paragraph(0, 0, 0, 0, 0, 0, 0, b"", None, [], [Control(18, 0, 8, 4, {"kind": 1, "number": 1}), ") 각주 본문"])
    assert compat.paragraph_text(p, "footnote", True) == " 각주 본문"
    assert compat.paragraph_text(p, "footnote", False) == ") 각주 본문"
    assert compat.paragraph_text(p, "body", True) == ") 각주 본문"


# ---------------- 쪽 보기(hwp3.render) ----------------

def _para_lines(text: str, lines: list[tuple]) -> bytes:
    """줄 정보를 직접 준 문단: lines = [(시작, 공백보정, 높이, pgy, sx, psx, 폭 또는 0x8003), ...]."""
    b = bytearray(para(hstr(text), nline=len(lines)))
    head = 1 + 2 + 2 + 1 + 1 + 4 + 1 + 31 + 187
    for k, ln in enumerate(lines):
        struct.pack_into("<7H", b, head + k * 14, *ln)
    # 대표 글자 모양 크기 250(10pt)
    struct.pack_into("<H", b, 12, 250)
    return bytes(b)


def test_render_follows_stored_lines_and_page_flags():
    from hwp3.render import PAGE_FLAG, render

    p1 = _para_lines("가나다", [(0, 0, 250, 0, 0, 0, 9000)])
    p2 = _para_lines("라마", [(0, 0, 250, 400, 100, 0, 9000)])
    p3 = _para_lines("바사", [(0, 0, 250, 0, 0, 0, PAGE_FLAG | 3)])  # 쪽 경계 표시: 새 쪽
    doc = parse_bytes(build(plist(p1, p2, p3)))
    pages, r = render(doc)
    assert len(pages) == 2
    lb = pages[0].lines[1]
    assert lb.text == "라마"
    assert lb.y == r.body_y + 400 and lb.x == r.body_x + 100
    assert pages[1].lines[0].text == "바사" and pages[1].lines[0].y == r.body_y


def test_render_half_width_model():
    """한글 97 배치는 영문을 반각(글자 크기 × 장평 × 0.5)으로 계산한다(양쪽 정렬 줄 4,373개 대조)."""
    from hwp3.render import Renderer, char_shape

    doc = parse_bytes(build(plist(para(hstr("가")))))
    r = Renderer(doc)
    cs = char_shape(struct.pack("<H", 250) + bytes(7) + bytes([100] * 7) + bytes(7) + bytes(8))
    assert r.glyph_width("가", cs) == 250 and r.glyph_width("A", cs) == 125 and r.glyph_width("─", cs) == 125
    cs2 = char_shape(struct.pack("<H", 250) + bytes(7) + bytes([90] * 7) + struct.pack("<7b", *[-10] * 7) + bytes(8))
    assert r.glyph_width("가", cs2) == 250 * 0.9 - 25


def test_render_reflow_breaks_long_paragraph_without_layout():
    from hwp3.render import render

    doc = parse_bytes(build(plist(para(hstr("가나다라 " * 60)))))  # 줄 정보 위치가 모두 0 → 직접 줄 나눔
    pages, r = render(doc)
    assert r.reflow
    body = [lb for lb in pages[0].lines if lb.where == "body"]
    assert len(body) > 1
    assert all(lb.text.strip().startswith("가") for lb in body)  # 어절 경계에서 끊긴다
    assert body[1].y > body[0].y


def test_box_arms_from_unicode_names():
    from hwp3.render import box_arms

    assert box_arms("┼") == {"up": "light", "down": "light", "left": "light", "right": "light"}
    assert box_arms("╞") == {"up": "light", "down": "light", "right": "double"}
    assert box_arms("━") == {"left": "heavy", "right": "heavy"}
    assert box_arms("가") == {}


def test_outline_label_uses_stored_numbers_and_decorations():
    from hwp3.model import Control
    from hwp3.render import outline_label

    def raw(level, nums, user, deco):
        b = struct.pack("<HBB", 1, 0, level) + struct.pack("<7H", *nums) + struct.pack("<7H", *user) + struct.pack("<14H", *deco)
        return (b + struct.pack("<H", 28)).ljust(60, b"\0").hex()

    user = (1, 0, 0, 5, 5, 5, 8)
    deco = (0, 46, 0, 41, 40, 41, 0, 46, 0, 41, 40, 41, 0, 0)  # SO-SUEOP: I.  1)  (1)  가.  가)  (가)
    lab = [outline_label(Control(28, 0, 64, 32, {"raw": raw(lv, (1, 2, 3, 4, 2, 3, 1), user, deco)})) for lv in range(6)]
    assert lab == ["I.", "2)", "(3)", "라.", "나)", "(다)"]
