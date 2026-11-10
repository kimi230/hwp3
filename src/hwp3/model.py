"""HWP3 문서 모델.

바이트 구조를 그대로 옮긴 값(raw)과 사람이 쓰기 좋은 해석값을 함께 둔다.
offset은 모두 압축을 푼 본문(body) 기준 바이트 위치다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from .charset import decode_hchar

# 특수 문자 코드(0~31) 이름. 규격의 특수 문자 표 순서.
CONTROL_NAMES = {
    0: "unused",
    1: "reserved1",
    2: "reserved2",
    3: "reserved3",
    4: "reserved4",
    5: "field",
    6: "bookmark",
    7: "date_format",
    8: "date_code",
    9: "tab",
    10: "box",  # 표/글상자/수식/버튼
    11: "picture",
    12: "reserved12",
    13: "para_end",
    14: "line",
    15: "hidden",
    16: "header_footer",
    17: "footnote",
    18: "auto_num",
    19: "new_num",
    20: "page_num",
    21: "page_num_ctrl",
    22: "mail_merge",
    23: "compose",
    24: "hyphen",
    25: "toc_mark",
    26: "index_mark",
    27: "reserved27",
    28: "outline",
    29: "reserved29",
    30: "keep_space",
    31: "fixed_space",
}

BOX_TYPES = {0: "table", 1: "textbox", 2: "equation", 3: "button"}


@dataclass
class Control:
    """문단 안의 특수 문자(코드 0~31)."""

    code: int
    offset: int
    size: int  # 본문에서 차지한 바이트 수(중첩 문단 리스트 포함)
    wsize: int  # 문단 글자 수(nch)에 더해지는 단위
    fields: dict = field(default_factory=dict)
    # 머리말/각주/숨은 설명/캡션 등 중첩 문단 리스트
    children: list[list[Paragraph]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return CONTROL_NAMES.get(self.code, f"ctrl{self.code}")

    def text(self) -> str:
        """본문 흐름 안에서 이 특수 문자를 글자로 볼 때의 표현."""
        if self.code == 9:
            return "\t"
        if self.code in (30, 31):
            return " "
        if self.code == 24:
            return "-"
        if self.code == 23:  # 글자 겹치기: 겹친 글자들
            return self.fields.get("text", "")
        return ""


@dataclass
class Cell:
    index: int
    x: int
    y: int
    w: int
    h: int
    raw: dict
    paragraphs: list[Paragraph] = field(default_factory=list)
    # Table.build_grid()가 채운다
    row: int = -1
    col: int = -1
    rowspan: int = 0
    colspan: int = 0

    def text(self, sep: str = "\n") -> str:
        return sep.join(p.text() for p in self.paragraphs).strip("\n")


@dataclass
class Table(Control):
    """코드 10 상자. box_type이 table이면 셀 격자를 만든다."""

    box_type: str = "table"
    cells: list[Cell] = field(default_factory=list)
    caption: list[Paragraph] = field(default_factory=list)
    n_rows: int = 0
    n_cols: int = 0
    row_edges: list[int] = field(default_factory=list)
    col_edges: list[int] = field(default_factory=list)
    # 표가 놓인 문단의 위치(1부터, 최상위 문단 순번)와 쪽
    para_index: int = -1
    page: int | None = None
    depth: int = 0  # 다른 표 안에 들어 있으면 1 이상
    table_id: int = -1  # 문서 안 순번(Document.iter_tables 순서)

    def build_grid(self) -> None:
        """셀 좌표(x, y, w, h)로 행·열과 병합 범위를 계산한다.

        열 경계는 셀 왼쪽 x들과 표 오른쪽 끝, 행 경계는 셀 위쪽 y들과 표 아래 끝이다.
        셀 오른쪽·아래 끝은 가장 가까운 경계에 붙인다. 실제 문서에는 끝 좌표가 이웃 셀 시작과
        1~3단위 어긋난 경우가 있어, 끝 좌표까지 경계로 쓰면 폭 1짜리 가짜 행·열이 생긴다.
        한컴오피스 변환본(HWPX)의 rowCnt/colCnt·셀 주소가 이 규칙과 일치한다(외부 시료 hwp3-sample16).
        """
        if not self.cells:
            return

        def edges(starts, ends):
            e = sorted(set(starts))
            if max(ends) > e[-1]:
                e.append(max(ends))
            return e

        def nearest(e, lo, v):
            return min(range(lo + 1, len(e)), key=lambda j: abs(e[j] - v)) if lo + 1 < len(e) else lo + 1

        xs = edges([c.x for c in self.cells], [c.x + c.w for c in self.cells])
        ys = edges([c.y for c in self.cells], [c.y + c.h for c in self.cells])
        self.col_edges = xs
        self.row_edges = ys
        xi = {v: i for i, v in enumerate(xs)}
        yi = {v: i for i, v in enumerate(ys)}
        for c in self.cells:
            c.col = xi[c.x]
            c.row = yi[c.y]
            c.colspan = nearest(xs, c.col, c.x + c.w) - c.col
            c.rowspan = nearest(ys, c.row, c.y + c.h) - c.row
        self.n_cols = len(xs) - 1
        self.n_rows = len(ys) - 1

    def grid_problems(self) -> list[str]:
        """격자 칸이 셀로 빠짐없이, 겹침 없이 채워졌는지 검사."""
        occ: dict[tuple[int, int], int] = {}
        problems = []
        for c in self.cells:
            if c.w <= 0 or c.h <= 0:
                problems.append(f"cell {c.index}: non-positive size w={c.w} h={c.h}")
                continue
            for r in range(c.row, c.row + c.rowspan):
                for k in range(c.col, c.col + c.colspan):
                    if (r, k) in occ:
                        problems.append(f"cell {c.index} overlaps cell {occ[(r, k)]} at ({r},{k})")
                    occ[(r, k)] = c.index
        holes = [(r, k) for r in range(self.n_rows) for k in range(self.n_cols) if (r, k) not in occ]
        if holes:
            problems.append(f"{len(holes)} grid slots not covered, first {holes[:5]}")
        return problems

    def matrix(self, fill_spans: bool = False) -> list[list[str | None]]:
        """행×열 문자열 행렬. 병합 셀은 왼쪽 위 칸에만 값을 두거나(fill_spans=False) 모두 채운다."""
        m: list[list[str | None]] = [[None] * self.n_cols for _ in range(self.n_rows)]
        for c in self.cells:
            t = c.text()
            for r in range(c.row, c.row + c.rowspan):
                for k in range(c.col, c.col + c.colspan):
                    if (r == c.row and k == c.col) or fill_spans:
                        m[r][k] = t
                    elif m[r][k] is None:
                        m[r][k] = None
        return m


Item = str | Control


@dataclass
class Paragraph:
    offset: int
    nch: int
    reuse_shape: int
    contain_cshape: int
    etcflag: int
    ctrlflag: int
    style: int
    char_shape: bytes
    para_shape: bytes | None
    lines: list[tuple[int, ...]]
    items: list[Item] = field(default_factory=list)
    # 최상위 본문이면 쪽 번호(줄 정보의 쪽 나눔 표시로 계산)
    page: int | None = None
    # 쪽 보기용: 글자 모양이 바뀌는 지점 (hchar 위치, 31바이트). 비어 있으면 문단 대표 모양(char_shape) 하나.
    cshape_runs: list[tuple[int, bytes]] = field(default_factory=list)
    # 쪽 보기용: (hchar 위치, 글자 문자열 또는 특수 문자). 위치는 줄 정보의 '줄 시작 위치'와 같은 단위.
    units: list[tuple[int, Item]] = field(default_factory=list)

    def text(self) -> str:
        out = []
        for it in self.items:
            if isinstance(it, str):
                out.append(it)
            else:
                out.append(it.text())
        return "".join(out)

    def controls(self) -> Iterator[Control]:
        for it in self.items:
            if isinstance(it, Control):
                yield it

    def page_breaks(self) -> list[tuple[int, str]]:
        """(줄 번호, 'page'|'column') — 이 줄 앞에서 쪽/단이 나뉜다."""
        out = []
        for i, ln in enumerate(self.lines):
            last = ln[-1]
            if last & 0x8000:
                out.append((i, "page" if last & 1 else "column"))
        return out


@dataclass
class Document:
    path: str | None
    size: int
    docinfo: dict
    summary: dict
    info_block: bytes
    body_size: int
    body_crc_ok: bool | None
    fonts: list[list[str]]
    styles: list[dict]
    paragraphs: list[Paragraph]
    tags: list[dict]  # 본문 뒤 추가 정보 블록(압축 영역)
    tail_tags: list[dict]  # 압축 영역 밖 추가 정보 블록
    warnings: list[str] = field(default_factory=list)
    unknown_chars: dict[int, int] = field(default_factory=dict)
    has_layout: bool = True  # 줄 정보에 쪽/단 경계가 저장돼 있는지
    embedded_images: dict[str, bytes] = field(default_factory=dict)  # 추가 정보 블록 ID 1(포함 그림)

    # ---- 순회 도우미 ----
    def iter_paragraphs(self, nested: bool = True) -> Iterator[tuple[int, Paragraph]]:
        """(깊이, 문단). nested=True면 표 셀·머리말·각주 안 문단도 낸다."""

        def walk(paras, depth):
            for p in paras:
                yield depth, p
                if not nested:
                    continue
                for c in p.controls():
                    if isinstance(c, Table):
                        for cell in c.cells:
                            yield from walk(cell.paragraphs, depth + 1)
                        yield from walk(c.caption, depth + 1)
                    for ch in c.children:
                        yield from walk(ch, depth + 1)

        yield from walk(self.paragraphs, 0)

    def iter_tables(self) -> Iterator[Table]:
        for _, p in self.iter_paragraphs(nested=True):
            for c in p.controls():
                if isinstance(c, Table):
                    yield c

    def text(self) -> str:
        """최상위 본문 문단의 글자. 표는 [표 n] 자리표시."""
        out = []
        for p in self.paragraphs:
            out.append(p.text())
        return "\n".join(out)

    def char_stats(self) -> dict:
        return {f"0x{k:04X}": v for k, v in sorted(self.unknown_chars.items())}


__all__ = ["BOX_TYPES", "Cell", "Control", "Document", "Paragraph", "Table", "decode_hchar"]
