"""HWP3 문서 → 엑셀(.xlsx). openpyxl이 필요하다.

시트 구성
  본문   최상위 문단을 한 줄씩(문단 번호, 쪽, 글). 표 자리는 '[표 T001]'처럼 시트 이름으로 표시한다.
  목록   표 시트마다 한 줄: 종류, 쪽, 문단 번호, 행·열 수, 표 번호(table_id), 바깥 표, 제목(캡션), 비고.
  T###   표 하나. A1이 격자의 (0행, 0열)이고 시트에는 격자만 둔다.
           view="hancom": 한컴식 격자(compat.table_views). 이름은 1부터, hancom/tables/T###.csv와 같다.
           view="raw":    원본 격자(Table.cells). 이름은 table_id, tables/T###.csv와 같다.
  X###   괘선 글자로 그린 표(texttable.find_text_tables). 세로 괘선 위치로 칸을 나눈다.

셀
  글자      셀 안 문단을 줄바꿈으로 이은 글(Cell.text()와 같음). 모르는 글자는 다른 내보내기와 같이 '□'.
            '='로 시작하는 글도 수식이 아니라 글자로 넣는다.
  병합      rowspan·colspan → merge_cells. 겹치는 범위는 병합하지 않고 '목록' 비고에 적는다.
  테두리    셀 선 종류 0~4 → 없음·thin·medium·dotted·double. 대각선은 thin.
  음영      음영 비율(%) → 회색 채움.
  세로 정렬 0 위, 1 가운데, 2 아래.
  글자 크기 셀 첫 문단의 대표 글자 크기(pt). 열 너비·행 높이가 원본 크기 기준이라 함께 옮긴다.
  크기      col_edges·row_edges(hunit = 1/1800인치) → 열 너비(글자 수 단위)·행 높이(pt).

numbers=True 이면 쉼표로 자리 나눈 숫자('56,352')만 숫자로 바꾸고 표시 형식을 '#,##0'으로 둔다.
그 밖의 글('-', 빈칸, '1998', '001')은 그대로 둔다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import compat
from .export import clean
from .model import Control, Document, Paragraph, Table
from .texttable import TextTable, find_text_tables

BODY_SHEET = "본문"
INDEX_SHEET = "목록"
LINE_STYLES = {1: "thin", 2: "medium", 3: "dotted", 4: "double"}
KIND_LABEL = {"table": "표", "textbox": "글상자", "button": "버튼", "equation": "수식", "texttable": "글자 표"}
MAX_ROWS, MAX_COLS, MAX_CELL_CHARS = 1_048_576, 16_384, 32_767
MAX_COL_WIDTH, MAX_ROW_HEIGHT = 255.0, 409.0
HUNIT_PER_INCH = 1800
HUNIT_PER_PT = 25

_NUMBER = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
# 엑셀(XML 1.0)에 넣을 수 없는 제어 문자. openpyxl의 ILLEGAL_CHARACTERS_RE와 같다.
_ILLEGAL = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")


def parse_number(s: str) -> tuple[int | float, str] | None:
    """쉼표로 자리 나눈 숫자면 (값, 표시 형식). 아니면 None."""
    t = s.strip()
    if not _NUMBER.match(t):
        return None
    body = t.replace(",", "")
    if len(body.lstrip("-").replace(".", "")) > 15:  # 엑셀 숫자는 15자리까지만 정확하다
        return None
    if "." in body:
        return float(body), "#,##0." + "0" * len(body.split(".")[1])
    return int(body), "#,##0"


def col_width(hunit: int) -> float:
    """hunit → 엑셀 열 너비(기본 글꼴 숫자 폭 7px, 여백 5px 기준)."""
    px = hunit * 96 / HUNIT_PER_INCH
    return round(min(MAX_COL_WIDTH, max(0.5, (px - 5) / 7)), 2)


def row_height(hunit: int) -> float:
    return round(min(MAX_ROW_HEIGHT, max(1.0, hunit / HUNIT_PER_PT)), 2)


def cell_value(paragraphs: list[Paragraph]) -> str:
    """셀 글자: 문단을 줄바꿈으로 잇고 앞뒤 빈 줄을 뗀다(Cell.text()와 같다). 모르는 글자는 □."""
    return clean("\n".join(p.text() for p in paragraphs).strip("\n"))


@dataclass
class _GridCell:
    row: int
    col: int
    rowspan: int
    colspan: int
    text: str
    raw: dict | None = None  # 원본 셀 정보(선 종류·음영·세로 정렬…). 없으면 모양을 넣지 않는다
    font_pt: float | None = None
    boxed: bool = False  # 글상자·버튼: 선 종류가 모두 0이면 테두리 없음


@dataclass
class _Sheet:
    name: str
    kind: str
    rows: int
    cols: int
    cells: list[_GridCell]
    col_hunits: list[int] = field(default_factory=list)
    row_hunits: list[int] = field(default_factory=list)
    col_chars: list[int] = field(default_factory=list)  # 글자 표: 칸 폭(표시 칸 수)
    page: int | None = None
    para: str = ""
    table_id: int | None = None
    depth: int = 0
    caption: str = ""
    notes: list[str] = field(default_factory=list)
    source: Table | None = None
    borders: dict[tuple[int, int], dict[str, str]] = field(default_factory=dict)  # 글자 표: (행, 열) → 변별 선


def _font_pt(paragraphs: list[Paragraph]) -> float | None:
    if not paragraphs:
        return None
    size = int.from_bytes(paragraphs[0].char_shape[:2], "little") if len(paragraphs[0].char_shape) >= 2 else 0
    pt = size / HUNIT_PER_PT
    return round(pt, 1) if 4 <= pt <= 72 else None


def _diffs(edges: list[int], n: int) -> list[int]:
    d = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]
    if len(d) == n:
        return d
    return [edges[-1] - edges[0]] if n == 1 and len(edges) >= 2 else []


def _table_sheets(doc: Document, view: str) -> list[_Sheet]:
    out: list[_Sheet] = []
    if view == "hancom":
        for n, v in enumerate(compat.table_views(doc), 1):
            t = v.source
            cells = [
                _GridCell(g.row, g.col, g.rowspan, g.colspan, cell_value(g.paragraphs),
                          g.source.raw if g.source is not None else None, _font_pt(g.paragraphs), boxed=g.origin == "box")
                for g in v.cells
            ]
            sh = _Sheet(f"T{n:03d}", v.kind, v.rows, v.cols, cells, _diffs(t.col_edges, v.cols), _diffs(t.row_edges, v.rows),
                        page=t.page, para=str(t.para_index), table_id=t.table_id, depth=t.depth, source=t)
            filled = sum(1 for g in v.cells if g.origin == "filled")
            extended = sum(1 for g in v.cells if g.origin == "extended")
            if filled:
                sh.notes.append(f"빈칸 {filled}개 채움")
            if extended:
                sh.notes.append(f"틈 메우려 늘린 셀 {extended}개")
            out.append(sh)
    elif view == "raw":
        for t in doc.iter_tables():
            if t.box_type != "table":
                continue
            cells = [_GridCell(c.row, c.col, c.rowspan, c.colspan, cell_value(c.paragraphs), c.raw, _font_pt(c.paragraphs)) for c in t.cells]
            sh = _Sheet(f"T{t.table_id:03d}", "table", t.n_rows, t.n_cols, cells, _diffs(t.col_edges, t.n_cols), _diffs(t.row_edges, t.n_rows),
                        page=t.page, para=str(t.para_index), table_id=t.table_id, depth=t.depth, source=t)
            holes = t.n_rows * t.n_cols - len({(r, k) for c in t.cells for r in range(c.row, c.row + c.rowspan) for k in range(c.col, c.col + c.colspan)})
            if holes:
                sh.notes.append(f"셀 없는 칸 {holes}개")
            out.append(sh)
    else:
        raise ValueError(f"view는 'hancom' 또는 'raw': {view!r}")
    for sh in out:
        cap = " / ".join(q.text() for q in sh.source.caption).strip(" /")
        sh.caption = clean(cap)
    return out


def text_table_grid(tt: TextTable) -> tuple[list[int], list[list[tuple[int, int, str]]], list[str | None]]:
    """글자 표 → (열 경계의 표시 칸 위치, 줄마다 [(열, 열 수, 글자)], 줄 위 가로 괘선 종류).

    열 경계는 표 안 모든 줄의 세로 괘선 위치를 모은 것이다. 가로 괘선뿐인 줄은 행으로 내지 않고,
    바로 아래 행의 위쪽 선(thin·medium·double)으로만 남긴다. 마지막 원소는 맨 아래 선이다.
    세로 괘선이 둘 미만이면 표가 아니다('═══ 제목 ═══' 같은 꾸밈 줄): 행을 내지 않는다.
    """
    edges = sorted({s for ln in tt.lines for s in ln.separators})
    if len(edges) < 2:
        return edges, [], []
    index = {e: i for i, e in enumerate(edges)}
    rows: list[list[tuple[int, int, str]]] = []
    rules: list[str | None] = []
    pending: str | None = None
    for ln in tt.lines:
        if ln.is_rule:
            pending = "double" if "═" in ln.text else "medium" if "━" in ln.text else "thin"
            continue
        row = []
        for s in ln.segments:
            if s.rule or not s.text:
                continue
            start = index.get(s.x0 - 1)
            end = index.get(s.x1)
            if start is None:  # 세로 괘선 없이 쓴 줄: 표 전체 폭에 걸친 한 칸
                start, end = 0, len(edges) - 1
            elif end is None:  # 오른쪽 테두리 없이 끝난 칸: 표 오른쪽 끝까지. 마지막 괘선 뒤 글은 덧붙인 열 하나
                end = max(start + 1, len(edges) - 1)
            row.append((start, max(1, end - start), s.text))
        rows.append(row)
        rules.append(pending)
        pending = None
    rules.append(pending)
    return edges, rows, rules


def _text_table_sheets(doc: Document) -> list[_Sheet]:
    lines = [p.text() for p in doc.paragraphs]
    out: list[_Sheet] = []
    for tt in find_text_tables(lines):
        edges, rows, rules = text_table_grid(tt)
        if not any(rows):
            continue  # 가로줄만 있는 구간(구분선)은 표가 아니다
        n_cols = max(max((c + span for c, span, _ in row), default=0) for row in rows)
        n_cols = max(n_cols, len(edges) - 1)
        widths = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]
        widths += [max(2, max((len(t) * 2 for row in rows for c, _, t in row if c >= len(edges) - 1), default=2))] * (n_cols - len(widths))
        sh = _Sheet(f"X{len(out) + 1:03d}", "texttable", len(rows), n_cols, [], col_chars=widths,
                    page=doc.paragraphs[tt.start].page, para=f"{tt.start + 1}-{tt.end + 1}")
        for r, row in enumerate(rows):
            for c, span, text in row:
                sh.cells.append(_GridCell(r, c, 1, span, clean(text)))
                for k in range(c, c + span):
                    b = sh.borders.setdefault((r, k), {})
                    if k == c:
                        b["left"] = "thin"
                    if k == c + span - 1:
                        b["right"] = "thin"
            for k in range(n_cols):
                if rules[r]:
                    sh.borders.setdefault((r, k), {})["top"] = rules[r]
                if r == len(rows) - 1 and rules[r + 1]:
                    sh.borders.setdefault((r, k), {})["bottom"] = rules[r + 1]
        out.append(sh)
    return out


def _inline(p: Paragraph, names: dict[int, str]) -> str:
    """본문 시트의 문단 글. 표는 시트 이름으로, 그 밖의 개체는 text.txt와 같은 표기로 적는다."""
    out: list[str] = []
    for it in p.items:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, Table):
            name = names.get(id(it))
            if name:
                out.append(f"[표 {name}]")
            else:
                inner = " ".join(_inline(q, names) for c in it.cells for q in c.paragraphs).strip()
                if inner:
                    out.append(f"[{KIND_LABEL.get(it.box_type, it.box_type)}: {inner}]")
        elif isinstance(it, Control) and it.code in (15, 16, 17):
            if it.code == 15:
                label = "숨은 설명"
            elif it.code == 16:
                label = "머리말" if it.fields.get("kind") == "header" else "꼬리말"
            else:
                label = "각주" if it.fields.get("kind") == "footnote" else "미주"
            inner = " ".join(_inline(q, names) for ch in it.children for q in ch).strip()
            if inner:
                out.append(f"[{label}: {inner}]")
        elif isinstance(it, Control) and it.code == 11:
            inner = " ".join(_inline(q, names) for ch in it.children for q in ch).strip()
            label = it.fields.get("path", "") or ("그리기" if it.fields.get("pictype") == 3 else "")
            out.append(f"[그림 {label}: {inner}]" if inner else f"[그림 {label}]".replace(" ]", "]"))
        else:
            out.append(it.text())
    return clean("".join(out))


def _parents(sheets: list[_Sheet], view_cells: dict[str, list[tuple[int, int, list[Paragraph]]]]) -> dict[int, str]:
    """표 안 표: id(안쪽 Table) → '바깥 시트!셀 주소'."""
    from openpyxl.utils import get_column_letter

    out: dict[int, str] = {}

    def walk(paras: list[Paragraph], where: str) -> None:
        for p in paras:
            for c in p.controls():
                if isinstance(c, Table):
                    out.setdefault(id(c), where)
                else:
                    for ch in c.children:
                        walk(ch, where)

    for sh in sheets:
        for r, k, paras in view_cells.get(sh.name, []):
            walk(paras, f"{sh.name}!{get_column_letter(k + 1)}{r + 1}")
    return out


def write_xlsx(doc: Document, path, view: str = "hancom", numbers: bool = False, text_tables: bool = True) -> dict:
    """doc을 path(.xlsx)에 쓴다. 결과 요약(시트 수, 숫자로 바꾼 칸 수 등)을 돌려준다."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.hyperlink import Hyperlink

    sheets = _table_sheets(doc, view)
    names = {id(sh.source): sh.name for sh in sheets}
    if view == "hancom":
        view_cells = {f"T{n:03d}": [(g.row, g.col, g.paragraphs) for g in v.cells] for n, v in enumerate(compat.table_views(doc), 1)}
    else:
        view_cells = {sh.name: [(c.row, c.col, c.paragraphs) for c in sh.source.cells] for sh in sheets}
    parents = _parents(sheets, view_cells)
    xsheets = _text_table_sheets(doc) if text_tables else []

    stats = {"numbers_converted": 0, "illegal_chars_removed": 0, "truncated_cells": 0, "merges": 0, "merges_skipped": 0}
    sides: dict[str, Side] = {}
    fills: dict[int, PatternFill] = {}
    fonts: dict[float, Font] = {}
    aligns = {v: Alignment(wrap_text=True, vertical=v) for v in ("top", "center", "bottom")}

    def side(style: str | None) -> Side:
        if style not in sides:
            sides[style] = Side(style=style)
        return sides[style]

    def put(ws, row: int, col: int, text: str, convert: bool = False):
        """글자를 (row, col)(1부터)에 넣는다. 항상 글자 형식이고, convert면 쉼표 숫자만 숫자로."""
        cell = ws.cell(row=row, column=col)
        if convert:
            num = parse_number(text)
            if num is not None:
                cell.value, cell.number_format = num
                stats["numbers_converted"] += 1
                return cell
        text, n = _ILLEGAL.subn("", text)
        stats["illegal_chars_removed"] += n
        if len(text) > MAX_CELL_CHARS:
            text = text[:MAX_CELL_CHARS]
            stats["truncated_cells"] += 1
        cell.value = text or None  # 같은 칸에 셀이 둘이면 마지막 셀 글이 남는다(matrix()와 같다)
        if text:
            cell.data_type = "s"  # '=…', '#N/A' 같은 글도 수식·오류값이 아니라 글자로
        cell.number_format = "General"
        return cell

    wb = Workbook()
    body = wb.active
    body.title = BODY_SHEET
    index = wb.create_sheet(INDEX_SHEET)
    bold = Font(bold=True)

    # ---- 본문 ----
    for k, h in enumerate(("문단", "쪽", "글"), 1):
        put(body, 1, k, h).font = bold
    for i, p in enumerate(doc.paragraphs, 1):
        body.cell(row=i + 1, column=1).value = i
        if p.page is not None:
            body.cell(row=i + 1, column=2).value = p.page
        put(body, i + 1, 3, _inline(p, names)).alignment = aligns["top"]
    body.column_dimensions["A"].width = 7
    body.column_dimensions["B"].width = 6
    body.column_dimensions["C"].width = 110
    body.freeze_panes = "A2"

    # ---- 표 시트 ----
    skipped: list[str] = []
    written: list[_Sheet] = []
    for sh in sheets + xsheets:
        if sh.rows > MAX_ROWS or sh.cols > MAX_COLS:
            skipped.append(f"{sh.name}: {sh.rows}x{sh.cols}은 엑셀 한계를 넘음")
            continue
        ws = wb.create_sheet(sh.name)
        written.append(sh)
        anchors: dict[tuple[int, int], int] = {}
        for c in sh.cells:
            anchors[(c.row, c.col)] = anchors.get((c.row, c.col), 0) + 1
        dup = sum(n - 1 for n in anchors.values() if n > 1)
        if dup:
            sh.notes.append(f"같은 칸에서 시작하는 셀 {dup}개(마지막 셀 글만 남음)")
        merged: set[tuple[int, int]] = set()
        todo = []
        for c in sh.cells:
            r0, k0 = c.row, c.col
            if not (0 <= r0 < sh.rows and 0 <= k0 < sh.cols):
                sh.notes.append(f"격자 밖 셀 ({r0},{k0})")
                continue
            r1 = min(sh.rows, r0 + max(1, c.rowspan)) - 1
            k1 = min(sh.cols, k0 + max(1, c.colspan)) - 1
            cell = put(ws, r0 + 1, k0 + 1, c.text, numbers)
            if c.font_pt:
                if c.font_pt not in fonts:
                    fonts[c.font_pt] = Font(size=c.font_pt)
                cell.font = fonts[c.font_pt]
            raw = c.raw
            vertical = {1: "center", 2: "bottom"}.get(raw.get("vcenter", 0), "top") if raw else "top"
            cell.alignment = aligns[vertical]
            if raw:
                lt = (list(raw.get("linetype", [])) + [0, 0, 0, 0])[:4]
                if not (c.boxed and not any(lt)):
                    d = raw.get("diagonal", 0) & 3
                    cell.border = Border(left=side(LINE_STYLES.get(lt[0])), right=side(LINE_STYLES.get(lt[1])),
                                         top=side(LINE_STYLES.get(lt[2])), bottom=side(LINE_STYLES.get(lt[3])),
                                         diagonal=side("thin" if d else None), diagonalDown=d in (1, 3), diagonalUp=d in (2, 3))
                shade = min(int(raw.get("shade", 0) or 0), 100)
                if shade > 0:
                    if shade not in fills:
                        g = round(255 * (1 - shade / 100))
                        fills[shade] = PatternFill("solid", fgColor=f"FF{g:02X}{g:02X}{g:02X}")
                    cell.fill = fills[shade]
            if (r1, k1) != (r0, k0):
                todo.append((r0, k0, r1, k1))
        # 병합: 다른 병합 범위와 겹치거나 다른 셀의 시작 칸을 덮으면 하지 않는다(엑셀이 깨진 파일로 본다)
        for r0, k0, r1, k1 in todo:
            area = {(r, k) for r in range(r0, r1 + 1) for k in range(k0, k1 + 1)}
            if area & merged or any(a in anchors for a in area if a != (r0, k0)) or anchors[(r0, k0)] > 1:
                stats["merges_skipped"] += 1
                sh.notes.append(f"겹쳐서 병합 안 함 {get_column_letter(k0 + 1)}{r0 + 1}:{get_column_letter(k1 + 1)}{r1 + 1}")
                continue
            merged |= area
            ws.merge_cells(start_row=r0 + 1, start_column=k0 + 1, end_row=r1 + 1, end_column=k1 + 1)
            stats["merges"] += 1
        for (r, k), b in sh.borders.items():  # 글자 표의 괘선
            cur = ws.cell(row=r + 1, column=k + 1).border
            ws.cell(row=r + 1, column=k + 1).border = Border(
                left=side(b.get("left")) if "left" in b else cur.left, right=side(b.get("right")) if "right" in b else cur.right,
                top=side(b.get("top")) if "top" in b else cur.top, bottom=side(b.get("bottom")) if "bottom" in b else cur.bottom)
        for k, w in enumerate(sh.col_hunits):
            ws.column_dimensions[get_column_letter(k + 1)].width = col_width(w)
        for k, w in enumerate(sh.col_chars):
            ws.column_dimensions[get_column_letter(k + 1)].width = min(MAX_COL_WIDTH, w + 1)
        for r, h in enumerate(sh.row_hunits):
            ws.row_dimensions[r + 1].height = row_height(h)

    # ---- 목록 ----
    heads = ("시트", "종류", "쪽", "문단", "행", "열", "셀", "표 번호", "바깥 표", "제목", "비고")
    for k, h in enumerate(heads, 1):
        put(index, 1, k, h).font = bold
    for i, sh in enumerate(written, 2):
        c = put(index, i, 1, sh.name)
        c.hyperlink = Hyperlink(ref=c.coordinate, location=f"'{sh.name}'!A1")
        c.style = "Hyperlink"
        put(index, i, 2, KIND_LABEL.get(sh.kind, sh.kind))
        if sh.page is not None:
            index.cell(row=i, column=3).value = sh.page
        if sh.para.isdigit():
            index.cell(row=i, column=4).value = int(sh.para)
        else:
            put(index, i, 4, sh.para)
        index.cell(row=i, column=5).value = sh.rows
        index.cell(row=i, column=6).value = sh.cols
        index.cell(row=i, column=7).value = len(sh.cells)
        if sh.table_id is not None:
            index.cell(row=i, column=8).value = sh.table_id
        put(index, i, 9, parents.get(id(sh.source), "") if sh.source is not None else "")
        put(index, i, 10, sh.caption)
        put(index, i, 11, "; ".join(sh.notes))
    for letter, w in zip("ABCDEFGHIJK", (8, 8, 6, 9, 6, 6, 6, 8, 12, 40, 50)):
        index.column_dimensions[letter].width = w
    index.freeze_panes = "A2"

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return {"xlsx": str(path), "xlsx_view": view, "xlsx_sheets": len(wb.sheetnames), "xlsx_tables": sum(1 for s in written if s.kind != "texttable"),
            "xlsx_text_tables": sum(1 for s in written if s.kind == "texttable"), "xlsx_skipped": skipped, **{f"xlsx_{k}": v for k, v in stats.items()}}
