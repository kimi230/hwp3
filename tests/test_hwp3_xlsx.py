"""HWP3 → 엑셀(hwp3.xlsx) 시험. 합성 문서로 병합·테두리·음영·세로 정렬·숫자 변환·겹친 셀·글자 표를 확인한다.

    pytest tests/test_hwp3_xlsx.py     (openpyxl 필요. 없으면 건너뛴다)
"""

from __future__ import annotations

import struct
import sys

import pytest

openpyxl = pytest.importorskip("openpyxl")

from hwp3 import decode_hchar, parse_bytes
from hwp3.__main__ import main
from hwp3.texttable import find_text_tables
from hwp3.xlsx import col_width, parse_number, row_height, text_table_grid, write_xlsx
from test_hwp3 import build, hstr, para, plist


def box(cells: list[dict], btype: int = 0) -> bytes:
    """상자(특수 문자 10). cells: x, y, w, h, text 또는 content(문단 리스트 bytes), vcenter, lines(왼·오·위·아래), shade, diagonal."""
    info = bytes(8) + struct.pack("<BBhhHH", 0, 0, 0, 0, 0, 10) + bytes(24)
    w = max(c["x"] + c["w"] for c in cells)
    h = max(c["y"] + c["h"] for c in cells)
    info += struct.pack("<HH", w, h) + bytes(6) + struct.pack("<HH", w, h) + bytes(4) + b"\x00\x00" + bytes(4) + bytes(4)
    info += struct.pack("<HH", 0, 0) + bytes(2) + struct.pack("<HHHH", 0, btype, len(cells), 0)
    assert len(info) == 84
    cinfo = b""
    for i, c in enumerate(cells):
        cinfo += struct.pack("<BBHHHHHHH", 0, i, 0, c["x"], c["y"], c["w"], c["h"], 0, 0) + bytes(2)
        cinfo += bytes([1, c.get("vcenter", 0)]) + bytes(c.get("lines", [1, 1, 1, 1])) + bytes([c.get("shade", 0), c.get("diagonal", 0), 0])
    lists = b"".join(c["content"] if "content" in c else plist(para(hstr(c["text"]))) for c in cells)
    return struct.pack("<HIH", 10, 0, 10) + info + cinfo + lists + plist()


def cell(x, y, w, h, text="", **kw) -> dict:
    return {"x": x, "y": y, "w": w, "h": h, "text": text, **kw}


# 3행 3열: A1:B1 가로 병합, C1:C2 세로 병합. 열 폭 1800·900·2700 hunit(1·0.5·1.5인치), 행 높이 500·250·750 hunit
GRID = [
    cell(0, 0, 2700, 500, "=SUM(A1)", lines=[2, 1, 4, 3], shade=20, vcenter=1),
    cell(2700, 0, 2700, 750, "56,352", lines=[1, 2, 2, 1], vcenter=2, diagonal=1),
    cell(0, 500, 1800, 250, "1,234.50", lines=[0, 0, 0, 0]),
    cell(1800, 500, 900, 250, "-"),
    cell(0, 750, 1800, 750, "1998"),
    cell(1800, 750, 900, 750, "#N/A"),
    cell(2700, 750, 2700, 750, " -7,000 ", diagonal=2),
]


def grid_doc() -> bytes:
    # 둘째 문단의 줄 정보에 쪽 경계 표시(bit15 + bit0)를 넣어 둘째 문단부터 2쪽이 되게 한다
    return build(plist(para(hstr("머리 글")), para(hstr("앞") + [box(GRID)] + hstr("뒤"), line_flag=0x8001), para(hstr("끝"))))


def load(path):
    return openpyxl.load_workbook(path)


def borders(c):
    b = c.border
    return [b.left.style, b.right.style, b.top.style, b.bottom.style]


def test_parse_number():
    assert parse_number("56,352") == (56352, "#,##0")
    assert parse_number(" -7,000 ") == (-7000, "#,##0")
    assert parse_number("1,234.50") == (1234.5, "#,##0.00")
    assert parse_number("1,234,567") == (1234567, "#,##0")
    for s in ("", "-", "1998", "001", "12,34", "1,2345", ",123", "1,234원", "+1,234", "1,234,567,890,123,456", "△1,234", "(1,234)"):
        assert parse_number(s) is None, s


def test_sizes():
    assert col_width(1800) == 13.0  # 1인치 = 96px → (96 - 5) / 7
    assert col_width(0) == 0.5 and col_width(10**7) == 255.0
    assert row_height(500) == 20.0 and row_height(0) == 1.0 and row_height(10**6) == 409.0


@pytest.mark.parametrize("view", ["hancom", "raw"])
def test_table_cells_merges_and_styles(tmp_path, view):
    doc = parse_bytes(grid_doc())
    name = "T001" if view == "hancom" else "T000"
    r = write_xlsx(doc, tmp_path / "a.xlsx", view=view)
    assert (r["xlsx_tables"], r["xlsx_merges"], r["xlsx_merges_skipped"], r["xlsx_numbers_converted"]) == (1, 2, 0, 0)
    wb = load(tmp_path / "a.xlsx")
    assert wb.sheetnames == ["본문", "목록", name]

    body = wb["본문"]
    assert [[c.value for c in row] for row in body.iter_rows()] == [
        ["문단", "쪽", "글"], [1, 1, "머리 글"], [2, 2, f"앞[표 {name}]뒤"], [3, 2, "끝"]]

    idx = wb["목록"]
    assert [c.value for c in idx[2]] == [name, "표", 2, 2, 3, 3, 7, 0, None, None, None]
    assert idx["A2"].hyperlink.location == f"'{name}'!A1"

    ws = wb[name]
    assert ws.dimensions == "A1:C3"
    assert sorted(str(m) for m in ws.merged_cells.ranges) == ["A1:B1", "C1:C2"]
    values = {c.coordinate: c.value for row in ws.iter_rows() for c in row if c.value is not None}
    assert values == {"A1": "=SUM(A1)", "C1": "56,352", "A2": "1,234.50", "B2": "-", "A3": "1998", "B3": "#N/A", "C3": " -7,000 "}
    assert all(c.data_type == "s" for row in ws.iter_rows() for c in row if c.value is not None)  # 수식·오류값으로 읽히지 않는다

    # 선 종류 0~4 → 없음·thin·medium·dotted·double (왼·오·위·아래)
    assert borders(ws["A1"]) == ["medium", "thin", "double", "dotted"]
    assert borders(ws["A2"]) == [None, None, None, None]
    assert borders(ws["B2"]) == ["thin", "thin", "thin", "thin"]
    # 병합 범위의 바깥 변에도 선이 간다: A1:B1의 오른쪽 변은 B1, C1:C2의 아래 변은 C2
    assert ws["B1"].border.right.style == "thin" and ws["B1"].border.top.style == "double"
    assert ws["C2"].border.bottom.style == "thin" and ws["C2"].border.right.style == "medium"
    # 대각선: 1 = \\, 2 = /
    assert ws["C1"].border.diagonal.style == "thin" and ws["C1"].border.diagonalDown and not ws["C1"].border.diagonalUp
    assert ws["C3"].border.diagonal.style == "thin" and ws["C3"].border.diagonalUp and not ws["C3"].border.diagonalDown
    assert ws["B2"].border.diagonal is None or ws["B2"].border.diagonal.style is None
    # 음영 20% → 회색 (255 × 0.8 = 204 = CC)
    assert ws["A1"].fill.fill_type == "solid" and ws["A1"].fill.fgColor.rgb == "FFCCCCCC"
    assert ws["B2"].fill.fill_type is None
    # 세로 정렬 0 위, 1 가운데, 2 아래. 줄바꿈 켬
    assert [ws[a].alignment.vertical for a in ("A1", "C1", "B2")] == ["center", "bottom", "top"]
    assert ws["A1"].alignment.wrap_text
    # 열 너비·행 높이
    assert [ws.column_dimensions[k].width for k in "ABC"] == [col_width(1800), col_width(900), col_width(2700)]
    assert [ws.row_dimensions[i].height for i in (1, 2, 3)] == [20.0, 10.0, 30.0]


def test_numbers_option(tmp_path):
    doc = parse_bytes(grid_doc())
    r = write_xlsx(doc, tmp_path / "n.xlsx", numbers=True)
    assert r["xlsx_numbers_converted"] == 3
    ws = load(tmp_path / "n.xlsx")["T001"]
    assert (ws["C1"].value, ws["C1"].number_format, ws["C1"].data_type) == (56352, "#,##0", "n")
    assert (ws["A2"].value, ws["A2"].number_format) == (1234.5, "#,##0.00")
    assert (ws["C3"].value, ws["C3"].number_format) == (-7000, "#,##0")
    # 쉼표 숫자가 아닌 글은 그대로 글자
    assert {a: (ws[a].value, ws[a].data_type) for a in ("A1", "B2", "A3", "B3")} == {
        "A1": ("=SUM(A1)", "s"), "B2": ("-", "s"), "A3": ("1998", "s"), "B3": ("#N/A", "s")}


def test_textbox_is_sheet_only_in_hancom_view(tmp_path):
    tb = box([cell(0, 0, 3000, 600, "상자 글", lines=[0, 0, 0, 0])], btype=1)
    data = build(plist(para(hstr("가") + [tb]), para([box([cell(0, 0, 500, 300, "갑"), cell(500, 0, 500, 300, "을")])])))
    doc = parse_bytes(data)
    write_xlsx(doc, tmp_path / "h.xlsx", view="hancom")
    wb = load(tmp_path / "h.xlsx")
    assert wb.sheetnames == ["본문", "목록", "T001", "T002"]
    assert [c.value for c in wb["목록"]["B"]][1:] == ["글상자", "표"]
    assert wb["T001"]["A1"].value == "상자 글" and borders(wb["T001"]["A1"]) == [None] * 4
    assert [c.value for c in wb["본문"]["C"]][1:] == ["가[표 T001]", "[표 T002]"]

    write_xlsx(doc, tmp_path / "r.xlsx", view="raw")
    wb = load(tmp_path / "r.xlsx")
    assert wb.sheetnames == ["본문", "목록", "T001"]  # table_id 1. 글상자(table_id 0)는 원본 격자 보기에서 시트가 아니다
    assert [c.value for c in wb["본문"]["C"]][1:] == ["가[글상자: 상자 글]", "[표 T001]"]
    assert [c.value for c in wb["T001"][1]] == ["갑", "을"]


def test_nested_table_points_to_outer_cell(tmp_path):
    inner = box([cell(0, 0, 400, 300, "안1"), cell(400, 0, 400, 300, "안2")])
    outer = box([cell(0, 0, 1000, 600, "바깥"), {"x": 1000, "y": 0, "w": 1000, "h": 600, "content": plist(para(hstr("칸") + [inner]))}])
    doc = parse_bytes(build(plist(para([outer]))))
    write_xlsx(doc, tmp_path / "x.xlsx")
    wb = load(tmp_path / "x.xlsx")
    assert wb.sheetnames == ["본문", "목록", "T001", "T002"]
    assert [c.value for c in wb["T001"][1]] == ["바깥", "칸"]
    assert [c.value for c in wb["T002"][1]] == ["안1", "안2"]
    assert [c.value for c in wb["목록"]["I"]][1:] == [None, "T001!B1"]
    assert wb["본문"]["C2"].value == "[표 T001]"


def test_overlapping_cells_do_not_make_overlapping_merges(tmp_path):
    cells = [
        cell(0, 0, 2000, 500, "넓은 칸"),       # A1:B1
        cell(1000, 0, 1000, 1000, "겹침"),      # B1:B2 — A1:B1과 B1에서 겹친다
        cell(0, 500, 1000, 500, "아래"),
    ]
    doc = parse_bytes(build(plist(para([box(cells)]))))
    t = next(doc.iter_tables())
    assert t.grid_problems()
    for view in ("hancom", "raw"):
        r = write_xlsx(doc, tmp_path / f"{view}.xlsx", view=view)
        assert r["xlsx_merges_skipped"] >= 1
        wb = load(tmp_path / f"{view}.xlsx")
        ws = wb[wb.sheetnames[2]]
        ranges = [m for m in ws.merged_cells.ranges]
        for i, a in enumerate(ranges):  # 병합 범위끼리 겹치지 않는다
            for b in ranges[i + 1:]:
                assert a.isdisjoint(b)
        m = t.matrix()
        assert [[c.value for c in row] for row in ws.iter_rows()] == m  # 칸 값은 matrix()와 같다
        assert "겹쳐서 병합 안 함" in wb["목록"]["K2"].value


def _hchar_of(ch: str) -> int:
    """글자 → 한글 내부 코드(괘선 문자용 역검색)."""
    for code in range(0x3000, 0x3100):
        if decode_hchar(code) == ch:
            return code
    raise AssertionError(f"괘선 문자 코드를 찾지 못함: {ch!r}")


def _hline(s: str) -> list[int]:
    return [_hchar_of(ch) if 0x2500 <= ord(ch) <= 0x257F else hstr(ch)[0] for ch in s]


# 표시 폭: 괘선·ASCII 1칸, 한글 2칸. 세로 괘선 위치는 0, 3, 12, 21
TEXT_TABLE = [
    "┌──┬────────┬────────┐",
    "│번│직종    │금액    │",
    "├──┼────────┴────────┤",
    "│ 1│갱부       56,352│",   # 가운데 괘선이 없는 줄: 두 열에 걸친 칸
    "│ 2│목공    │ 7,000  │",
    "└──┴────────┴────────┘",
]


def test_text_table_grid():
    (tt,) = find_text_tables(TEXT_TABLE)
    edges, rows, rules = text_table_grid(tt)
    assert edges == [0, 3, 12, 21]
    assert rows == [
        [(0, 1, "번"), (1, 1, "직종"), (2, 1, "금액")],
        [(0, 1, "1"), (1, 2, "갱부       56,352")],
        [(0, 1, "2"), (1, 1, "목공"), (2, 1, "7,000")],
    ]
    assert rules == ["thin", "thin", None, "thin"]


def test_text_table_sheet(tmp_path):
    doc = parse_bytes(build(plist(para(hstr("표 앞")), *[para(_hline(s)) for s in TEXT_TABLE], para(hstr("표 뒤")))))
    assert [p.text() for p in doc.paragraphs][1:-1] == TEXT_TABLE
    r = write_xlsx(doc, tmp_path / "t.xlsx", numbers=True)
    assert (r["xlsx_tables"], r["xlsx_text_tables"]) == (0, 1)
    wb = load(tmp_path / "t.xlsx")
    assert wb.sheetnames == ["본문", "목록", "X001"]
    assert [c.value for c in wb["목록"][2]][:7] == ["X001", "글자 표", None, "2-7", 3, 3, 8]  # 배치가 저장되지 않은 문서라 쪽은 비어 있다
    ws = wb["X001"]
    assert [[c.value for c in row] for row in ws.iter_rows()] == [["번", "직종", "금액"], ["1", "갱부       56,352", None], ["2", "목공", 7000]]
    assert [str(m) for m in ws.merged_cells.ranges] == ["B2:C2"]
    assert ws["A1"].border.top.style == "thin" and ws["A2"].border.top.style == "thin" and ws["A3"].border.top.style is None
    assert ws["C3"].border.bottom.style == "thin" and ws["C3"].border.right.style == "thin"
    # 본문 시트에는 괘선 줄이 그대로 남는다
    assert [c.value for c in wb["본문"]["C"]][2:-1] == TEXT_TABLE
    # 끄면 글자 표 시트를 만들지 않는다
    assert write_xlsx(doc, tmp_path / "t2.xlsx", text_tables=False)["xlsx_text_tables"] == 0


def test_rule_lines_without_vertical_bars_are_not_a_text_table(tmp_path):
    doc = parse_bytes(build(plist(para(_hline("────────")), para(hstr("글")), para(_hline("═══ 제목 ═══")), para(hstr("글")))))
    assert len(find_text_tables([p.text() for p in doc.paragraphs])) == 2
    assert write_xlsx(doc, tmp_path / "r.xlsx")["xlsx_text_tables"] == 0


def test_text_table_line_without_bars_spans_the_table():
    (tt,) = find_text_tables(["┌──┬──┐", "│가│나│", "─ 주 ─", "└──┴──┘"])
    edges, rows, _ = text_table_grid(tt)
    assert edges == [0, 3, 6] and rows == [[(0, 1, "가"), (1, 1, "나")], [(0, 2, "─ 주 ─")]]


def test_unknown_char_is_square_and_bad_view_rejected(tmp_path):
    doc = parse_bytes(build(plist(para([box([cell(0, 0, 500, 300, "가"), cell(500, 0, 500, 300, "나")])]))))
    t = next(doc.iter_tables())
    t.cells[0].paragraphs[0].items[0] = "가\U000F3073\x01나"  # 모르는 글자(사설 영역)와 엑셀에 넣을 수 없는 제어 문자
    r = write_xlsx(doc, tmp_path / "u.xlsx")
    assert r["xlsx_illegal_chars_removed"] == 1
    assert load(tmp_path / "u.xlsx")["T001"]["A1"].value == "가□나"
    with pytest.raises(ValueError):
        write_xlsx(doc, tmp_path / "v.xlsx", view="other")


def test_cli_xlsx(tmp_path, capsys):
    src = tmp_path / "in.hwp"
    src.write_bytes(grid_doc())
    assert main(["convert", str(src), "-o", str(tmp_path / "out"), "--xlsx", "--xlsx-view", "raw", "--numbers"]) == 0
    assert '"xlsx_numbers_converted": 3' in capsys.readouterr().out
    wb = load(tmp_path / "out" / "document.xlsx")
    assert wb.sheetnames == ["본문", "목록", "T000"] and wb["T000"]["C1"].value == 56352


def test_cli_xlsx_without_openpyxl_reports_and_keeps_other_outputs(tmp_path, capsys, monkeypatch):
    src = tmp_path / "in.hwp"
    src.write_bytes(grid_doc())
    monkeypatch.setitem(sys.modules, "openpyxl", None)  # openpyxl이 없는 환경
    assert main(["convert", str(src), "-o", str(tmp_path / "out"), "--xlsx"]) == 3
    assert "openpyxl" in capsys.readouterr().err
    assert (tmp_path / "out" / "document.json").exists() and not (tmp_path / "out" / "document.xlsx").exists()
