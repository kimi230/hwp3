"""한컴식 보기: 한컴오피스가 HWP3를 HWP5/HWPX로 바꿀 때와 같은 모양으로 표·글을 본다.

원본 모델(Document/Table/Cell)은 바꾸지 않고 보기 객체만 만든다. 규칙은 한컴 변환본(HWPX)과
대조해 확인한 것만 둔다(괄호 안은 근거가 된 공개 시료 이름).

  - 글상자, 하이퍼텍스트가 아닌 버튼 → 1×1 표 (SO-SUEOP 한컴 HWPX: 글상자 2 + 버튼 1 = 표 3)
  - 하이퍼텍스트 버튼(상자 '기타 옵션' bit 4) → 표 아님 (hwp3-sample16 빈 버튼 4개는 한컴 HWPX에 표 없음)
  - 수식 상자 → 표 아님, 수식 개체(원문은 equation_script로)
  - 셀이 없는 칸: 폭(또는 높이)이 GAP_TOL 이하이면 옆(위) 셀을 늘려 메우고, 아니면 1×1 빈 셀을 채운다
    (hwp3-sample16 1단위 틈, hwp3-table-grid-gap 빈칸 5개)
  - 표 제목: 위치 값 1(위)·2(왼쪽)는 셀 앞, 그 밖은 셀 뒤 (hwp3-table-caption 한컴 PDF 순서)
  - 각주·미주 본문: 문서 설정 '각주 번호 뒤 문자'가 ')'이면 본문 첫머리 [자동 번호][')'] 의 ')'는
    번호 모양으로 보고 본문 글에서 뺀다 (german-legislative-system)
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from .model import Cell, Control, Document, Paragraph, Table

GAP_TOL = 3  # HWPUNIT. 이보다 좁은 빈 열(행)은 옆 셀을 늘려 메운다
HYPERTEXT_BIT = 1 << 4
CAPTION_BEFORE = {1, 2}  # 위, 왼쪽


@dataclass
class GridCell:
    row: int
    col: int
    rowspan: int
    colspan: int
    paragraphs: list[Paragraph] = field(default_factory=list)
    origin: str = "cell"  # cell | extended(틈 메우려 늘림) | filled(빈칸 채움) | box(글상자·버튼)
    source: Cell | None = None  # 원본 셀(테두리·음영 등 모양을 볼 때). 채운 빈칸은 None

    def text(self, sep: str = "\n") -> str:
        return sep.join(p.text() for p in self.paragraphs)


@dataclass
class TableView:
    source: Table
    kind: str  # table | textbox | button
    rows: int
    cols: int
    cells: list[GridCell]
    caption: list[Paragraph]
    caption_before: bool


def is_hypertext(t: Table) -> bool:
    return t.box_type == "button" and bool(t.fields.get("option", 0) & HYPERTEXT_BIT)


def table_view(t: Table) -> TableView | None:
    """한컴식 표 하나. 표가 되지 않는 상자(수식, 하이퍼텍스트 버튼)는 None."""
    cap_before = t.fields.get("cap_pos") in CAPTION_BEFORE
    if t.box_type in ("textbox", "button"):
        if is_hypertext(t):
            return None
        paras = [p for c in t.cells for p in c.paragraphs]
        return TableView(t, t.box_type, 1, 1, [GridCell(0, 0, 1, 1, paras, "box", t.cells[0] if t.cells else None)], t.caption, cap_before)
    if t.box_type != "table":
        return None
    cells = [GridCell(c.row, c.col, c.rowspan, c.colspan, c.paragraphs, source=c) for c in t.cells]
    occ: dict[tuple[int, int], GridCell] = {}
    for g in cells:
        for r in range(g.row, g.row + g.rowspan):
            for k in range(g.col, g.col + g.colspan):
                occ.setdefault((r, k), g)
    xs, ys = t.col_edges, t.row_edges

    def width(k: int) -> int:
        return xs[k + 1] - xs[k] if k + 1 < len(xs) else 0

    def height(r: int) -> int:
        return ys[r + 1] - ys[r] if r + 1 < len(ys) else 0

    for r in range(t.n_rows):
        for k in range(t.n_cols):
            if (r, k) in occ:
                continue
            left = occ.get((r, k - 1)) if k > 0 else None
            up = occ.get((r - 1, k)) if r > 0 else None
            if (left is not None and width(k) <= GAP_TOL and left.col + left.colspan == k
                    and all((rr, k) not in occ for rr in range(left.row, left.row + left.rowspan))):
                left.colspan += 1
                left.origin = "extended" if left.origin == "cell" else left.origin
                for rr in range(left.row, left.row + left.rowspan):
                    occ[(rr, k)] = left
            elif (up is not None and height(r) <= GAP_TOL and up.row + up.rowspan == r
                  and all((r, kk) not in occ for kk in range(up.col, up.col + up.colspan))):
                up.rowspan += 1
                up.origin = "extended" if up.origin == "cell" else up.origin
                for kk in range(up.col, up.col + up.colspan):
                    occ[(r, kk)] = up
            else:
                g = GridCell(r, k, 1, 1, [], "filled")
                cells.append(g)
                occ[(r, k)] = g
    cells.sort(key=lambda g: (g.row, g.col))
    return TableView(t, "table", t.n_rows, t.n_cols, cells, t.caption, cap_before)


def table_views(doc: Document) -> Iterator[TableView]:
    """문서 순서(앞선 순회)대로 한컴식 표. 표 안 표도 낸다."""
    for t in doc.iter_tables():
        v = table_view(t)
        if v is not None:
            yield v


def _note_body_starts_with_suffix(doc: Document) -> bool:
    return doc.docinfo.get("footnote_char") == ord(")")


def iter_paragraphs(doc: Document) -> Iterator[tuple[str, Paragraph]]:
    """(맥락, 문단)을 한컴식 순서로: 표 제목 위치를 따르고, 수식 상자 안 문단은 내지 않는다."""

    def walk(paras: list[Paragraph], ctx: str):
        for p in paras:
            yield ctx, p
            for c in p.controls():
                if isinstance(c, Table):
                    if c.box_type == "equation":
                        continue
                    v = table_view(c)
                    before = v.caption_before if v else False
                    if before:
                        yield from walk(c.caption, "caption")
                    for cell in c.cells:
                        yield from walk(cell.paragraphs, "cell" if c.box_type == "table" else c.box_type)
                    if not before:
                        yield from walk(c.caption, "caption")
                for ch in c.children:
                    yield from walk(ch, c.name if c.name in ("footnote", "header_footer") else ctx)

    yield from walk(doc.paragraphs, "body")


def paragraph_text(p: Paragraph, ctx: str, strip_note_suffix: bool) -> str:
    items = list(p.items)
    if strip_note_suffix and ctx == "footnote" and len(items) >= 2 and isinstance(items[0], Control) \
            and items[0].name == "auto_num" and isinstance(items[1], str) and items[1].startswith(")"):
        items[1] = items[1][1:]
    return "".join(it for it in items if isinstance(it, str))


def text(doc: Document) -> str:
    """한컴식 본문 글: 각 문단의 자기 글자만, 위 규칙을 적용해 잇는다."""
    strip = _note_body_starts_with_suffix(doc)
    return "\n".join(paragraph_text(p, ctx, strip) for ctx, p in iter_paragraphs(doc))


def equation_scripts(doc: Document) -> list[str]:
    return ["\n".join(p.text() for c in t.cells for p in c.paragraphs) for t in doc.iter_tables() if t.box_type == "equation"]


def table_csv(v: TableView) -> str:
    """한컴식 표를 CSV로(병합 칸은 왼쪽 위 칸에만 글자)."""
    import csv
    import io

    m = [["" for _ in range(v.cols)] for _ in range(v.rows)]
    for g in v.cells:
        m[g.row][g.col] = g.text(" / ").replace("\t", " ")
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(m)
    return buf.getvalue()


def write_hancom(doc: Document, out_dir) -> dict:
    """한컴식 보기 결과를 out_dir/hancom/ 에 쓴다: text.txt, tables/T###.csv, equations.txt."""
    from pathlib import Path

    from .export import clean

    out = Path(out_dir) / "hancom"
    (out / "tables").mkdir(parents=True, exist_ok=True)
    (out / "text.txt").write_text(clean(text(doc)), encoding="utf-8")
    n = 0
    for n, v in enumerate(table_views(doc), 1):
        (out / "tables" / f"T{n:03d}.csv").write_text(clean(table_csv(v)), encoding="utf-8")
    eq = equation_scripts(doc)
    if eq:
        (out / "equations.txt").write_text("\n\n".join(eq), encoding="utf-8")
    return {"hancom_tables": n, "equations": len(eq)}
