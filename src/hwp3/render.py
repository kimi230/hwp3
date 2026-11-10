"""쪽 보기: 한글 97이 HWP3에 저장한 배치(줄 정보)를 그대로 따라 쪽마다 SVG를 만든다.

줄 정보 14바이트(규격 표 12, 예약 칸은 rhwp와 이 저장소 표본으로 확인):
  0 줄 시작 위치(hchar)  2 공백 보정(shunit)  4 줄 높이  6 세로 좌표 pgy(본문 위 기준)
  8 줄 시작 가로 위치 sx(본문 왼쪽 기준, 들여쓰기·정렬 반영)  10 psx(뜻 미확정)
  12 줄 폭 — 단, 쪽이 시작되는 줄은 0x8003(bit15 유효 + 쪽·단 경계)
좌표 단위는 hunit(1/1800인치). SVG viewBox도 hunit으로 둔다.

줄 나눔·쪽 나눔·줄 위치는 저장값을 쓰고, 줄 안 글자 간격만 글자 모양(크기·장평·자간)으로 계산한다.
배치가 저장되지 않은 파일(has_layout=False)은 세로 좌표가 줄어드는 곳을 쪽 경계로 본다.
"""

from __future__ import annotations

import base64
import html
import struct
import unicodedata
from dataclasses import dataclass, field

from .model import Control, Document, Paragraph, Table

PT = 25.0  # 1pt = 25 hunit
ASCENT = 0.85  # 줄 높이 중 기준선까지 비율(근사)
COLORS = ["#000000", "#0000ff", "#008000", "#008080", "#ff0000", "#800080", "#ffff00", "#ffffff"]
SANS_HINTS = ("고딕", "돋움", "굴림", "헤드라인", "산세리프", "중고", "견고", "태고", "gothic", "sans", "arial", "helvetica", "dotum", "gulim")
SERIF_FAMILY = "'Noto Serif KR', 'AppleMyungjo', 'Batang', 'Hiragino Mincho ProN', 'Songti SC', serif"
SANS_FAMILY = "'Noto Sans KR', 'Apple SD Gothic Neo', 'Malgun Gothic', 'Hiragino Sans', 'PingFang SC', sans-serif"
# 표시용 대체: Noto Serif/Sans KR에 글리프가 없는 기호를 모양이 같은 글자로(본문 텍스트·JSON은 바꾸지 않는다)
DISPLAY_SUBST = {"\u223c": "\uff5e", "\u2024": "\u00b7", "\u2219": "\u00b7", "\u22c5": "\u00b7", "\u02da": "\u00b0",
                 "\u21b3": "\u2514", "\u02d8": "\u02c7"}
PAGE_FLAG = 0x8000


# ---------------- 모양 ----------------

def char_shape(b: bytes) -> dict:
    """글자 모양 31바이트(규격 표 16)."""
    size = struct.unpack_from("<H", b, 0)[0]
    return {"size": size or 250, "font": list(b[2:9]), "ratio": [x or 100 for x in b[9:16]],
            "spacing": list(struct.unpack_from("<7b", b, 16)), "shade_color": b[23], "color": b[24],
            "shade_ratio": b[25], "attr": b[26]}


def para_shape(b: bytes | None) -> dict:
    """문단 모양 187바이트(규격 표 13)."""
    if not b:
        return {"left": 0, "right": 0, "indent": 0, "linesp": 160, "linesp_abs": False, "below": 0, "above": 0, "align": 0}
    left, right, indent, lsp, below = struct.unpack_from("<HHhHH", b, 0)
    return {"left": left, "right": right, "indent": indent, "linesp": lsp & 0x7FFF, "linesp_abs": bool(lsp & 0x8000),
            "below": below, "word_spacing": b[10], "align": b[11], "columns": b[172] or 1,
            "column_gap": struct.unpack_from("<H", b, 174)[0], "above": struct.unpack_from("<H", b, 183)[0]}


def lang_of(ch: str) -> int:
    """글꼴 언어(규격 '언어별 7종'): 0 한글 1 영문 2 한자 3 일어 4 기타 5 기호 6 사용자."""
    o = ord(ch[0])
    if 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F or 0xA960 <= o <= 0xA97F or 0xD7B0 <= o <= 0xD7FF:
        return 0
    if o < 0x80:
        return 1
    if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF or o >= 0x20000:
        return 2
    if 0x3040 <= o <= 0x30FF:
        return 3
    return 5


def is_half(ch: str) -> bool:
    """반각으로 그리는 글자: ASCII, 라틴 등 좁은 글자, 괘선(한글 97 글자 표가 반각 괘선으로 열을 맞춘다)."""
    o = ord(ch[0])
    if o < 0x80 or 0x2500 <= o <= 0x257F:
        return True
    return unicodedata.east_asian_width(ch[0]) in ("Na", "H", "N") and not (0x1100 <= o <= 0x11FF)


def _kind(ch: str) -> str:
    o = ord(ch[0])
    if ch == " ":
        return "space"
    if 0x2500 <= o <= 0x257F:
        return "box"
    return "ascii" if is_half(ch) else "full"


def box_arms(ch: str) -> dict[str, str]:
    """괘선 문자(U+2500~257F)의 팔 방향과 굵기를 유니코드 이름에서 읽는다. 예: 'DOWN SINGLE AND HORIZONTAL DOUBLE'."""
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return {}
    if not name.startswith("BOX DRAWINGS "):
        return {}
    words = name[len("BOX DRAWINGS "):].replace("DASH", "").split()
    weights = {"LIGHT": "light", "HEAVY": "heavy", "DOUBLE": "double", "SINGLE": "light"}
    dirs = {"UP": ["up"], "DOWN": ["down"], "LEFT": ["left"], "RIGHT": ["right"], "HORIZONTAL": ["left", "right"], "VERTICAL": ["up", "down"]}
    if "DIAGONAL" in words:
        rest = " ".join(words)
        if "CROSS" in rest:
            return {"diag1": "light", "diag2": "light"}
        return {"diag1": "light"} if "UPPER RIGHT TO LOWER LEFT" in rest else {"diag2": "light"}
    default = weights.get(words[0], "light")
    arms: dict[str, str] = {}
    for seg in " ".join(words).split(" AND "):
        sw = seg.split()
        wt = next((weights[w] for w in sw if w in weights), default)
        for w in sw:
            for d in dirs.get(w, []):
                arms[d] = wt
    return arms


# Noto Serif KR / Noto Sans KR(wght 400) 영문 0x20~0x7E 전진폭(1/1000 em). fontTools로 뽑은 값.
_SERIF_W = [256, 308, 376, 574, 550, 923, 779, 196, 364, 364, 478, 580, 327, 346, 327, 352, 557, 471, 558, 557, 555, 558, 558, 549,
            558, 562, 327, 327, 580, 580, 580, 432, 912, 718, 670, 691, 770, 652, 629, 741, 853, 405, 403, 731, 625, 975, 796, 767,
            641, 767, 715, 567, 659, 797, 714, 1053, 700, 687, 605, 344, 352, 344, 580, 563, 435, 557, 638, 538, 629, 547, 388, 566,
            663, 332, 311, 607, 334, 974, 661, 596, 638, 607, 462, 473, 367, 653, 548, 838, 564, 552, 498, 374, 316, 374, 580]
_SANS_W = [224, 323, 474, 555, 555, 921, 680, 278, 338, 338, 467, 555, 278, 347, 278, 392, 555, 555, 555, 555, 555, 555, 555, 555,
           555, 555, 278, 278, 555, 555, 555, 474, 946, 608, 657, 638, 688, 589, 552, 689, 728, 293, 535, 646, 543, 812, 723, 742,
           633, 742, 635, 596, 599, 721, 575, 878, 573, 531, 603, 338, 392, 338, 555, 559, 606, 563, 618, 510, 620, 554, 325, 564,
           607, 275, 275, 552, 284, 926, 610, 606, 620, 620, 388, 468, 377, 607, 521, 802, 498, 521, 475, 338, 270, 338, 555]
ASCII_SERIF = {chr(0x20 + i): w for i, w in enumerate(_SERIF_W)}
ASCII_SANS = {chr(0x20 + i): w for i, w in enumerate(_SANS_W)}


_ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
_HANGUL_SEQ = "가나다라마바사아자차카타파하"
_JAMO_SEQ = "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ"
# 한글 97 기본 개요(형태 1~5): 단계별 (모양 코드, 앞, 뒤). SO-SUEOP 한컴 PDF의 I. 1) (1) 가. 가) (가) 와 같다.
_DEFAULT_OUTLINE = [(1, "", "."), (0, "", ")"), (0, "(", ")"), (5, "", "."), (5, "", ")"), (5, "(", ")"), (7, "", "")]


def _roman(n: int) -> str:
    out = ""
    for v, r in _ROMAN:
        while n >= v:
            out, n = out + r, n - v
    return out


def format_number(n: int, code: int) -> str:
    """개요 모양 코드(메뉴 순서) → 번호 글자. 0 아라비아, 1 로마 대문자, 5 가나다는 한컴 PDF로 확인.
    나머지(2 로마 소문자, 3·4 로마자, 6 ㄱㄴㄷ, 7 원 숫자)는 메뉴 순서로 추정한 값이다."""
    if n <= 0:
        return ""
    if code == 1:
        return _roman(n)
    if code == 2:
        return _roman(n).lower()
    if code == 3:
        return chr(ord("A") + (n - 1) % 26)
    if code == 4:
        return chr(ord("a") + (n - 1) % 26)
    if code == 5:
        return _HANGUL_SEQ[(n - 1) % len(_HANGUL_SEQ)]
    if code == 6:
        return _JAMO_SEQ[(n - 1) % len(_JAMO_SEQ)]
    if code == 7 and n <= 20:
        return chr(0x2460 + n - 1)
    return str(n)


def outline_label(c: Control) -> str:
    """개요 번호 특수 문자(28)의 표시 글자. 종류 0(모양 정의)은 글자가 없다."""
    from .charset import decode_hchar

    raw = bytes.fromhex(c.fields.get("raw", ""))
    if len(raw) < 60:
        return ""
    kind, form, level = struct.unpack_from("<HBB", raw, 0)
    if kind != 1 or level > 6:
        return ""
    nums = struct.unpack_from("<7H", raw, 4)
    user = struct.unpack_from("<7H", raw, 18)
    deco = struct.unpack_from("<14H", raw, 32)
    n = nums[level]
    if form in (1, 2, 3, 4, 5):
        code, pre, post = _DEFAULT_OUTLINE[level]
        return pre + format_number(n, code) + post
    code = user[level]
    pre = decode_hchar(deco[level * 2]) or "" if deco[level * 2] else ""
    post = decode_hchar(deco[level * 2 + 1]) or "" if deco[level * 2 + 1] else ""
    if code == 128:
        return ""
    if code > 12:  # 불릿 문자
        return pre + (decode_hchar(code) or "") + post
    return pre + format_number(n, code) + post


# ---------------- 결과 구조 ----------------

@dataclass
class LineBox:
    page: int
    x: float  # 줄 시작(hunit, 종이 왼쪽 기준)
    y: float  # 줄 위
    height: float
    baseline: float
    text: str
    where: str  # body | cell | header | footer | footnote | textbox | caption


@dataclass
class Page:
    number: int
    width: int
    height: int
    svg: list[str] = field(default_factory=list)
    lines: list[LineBox] = field(default_factory=list)
    footnotes: list[Control] = field(default_factory=list)


# ---------------- 렌더러 ----------------

class Renderer:
    def __init__(self, doc: Document):
        self.doc = doc
        di = doc.docinfo
        self.W = di["paper_width"] or 14882
        self.H = di["paper_height"] or 21047
        if di.get("paper_orientation") == 1 and self.W < self.H:
            self.W, self.H = self.H, self.W
        self.ml, self.mr, self.mt, self.mb = di["margin_left"], di["margin_right"], di["margin_top"], di["margin_bottom"]
        self.hl, self.fl, self.gutter = di["header_len"], di["footer_len"], di["gutter"]
        self.body_x = self.ml + self.gutter
        self.body_y = self.mt + self.hl
        self.body_w = self.W - self.ml - self.mr - self.gutter
        self.body_bottom = self.H - self.mb - self.fl
        self.pages: list[Page] = []
        self.hf_events: list[tuple[int, str, Control]] = []  # (쪽 순번, header|footer, 특수 문자 16)
        self.pagenum_events: list[tuple[int, int, int]] = []  # (쪽 순번, 위치 0~8, 모양)
        self.newnum_events: list[tuple[int, int]] = []  # (쪽 순번, 새 쪽 번호) — 특수 문자 19 종류 0
        self.flags = doc.has_layout
        # 줄 정보에 위치가 하나도 없으면(다른 프로그램이 저장한 HWP3) 줄·쪽 나눔을 직접 계산한다
        self.reflow = not doc.has_layout and not any(ln[3] or ln[4] or ln[6] for q in doc.paragraphs for ln in q.lines)
        self._tab_origin = 0.0
        self._line_cache: dict[tuple[int, float], list[tuple]] = {}
        self.images = doc.embedded_images
        self.converted: dict[str, bytes] = {}  # WMF 등 브라우저가 못 그리는 그림을 바꾼 PNG(convert_images)
        self.warnings: list[str] = []

    # ---- 쪽 ----
    def new_page(self) -> Page:
        pg = Page(len(self.pages) + (self.doc.docinfo.get("begin_page_no") or 1), self.W, self.H)
        self.pages.append(pg)
        return pg

    def run(self) -> list[Page]:
        page = self.new_page()
        shape = para_shape(None)
        first = True
        body_h = self.body_bottom - self.body_y
        if self.reflow:
            y = 0.0
            for p in self.doc.paragraphs:
                if p.para_shape is not None:
                    shape = para_shape(p.para_shape)
                lines = self.lines_for(p, shape, self.body_w)
                if p.etcflag & 2 and not first:  # 문단 '페이지 나눔'
                    page, y = self.new_page(), 0.0
                first = False
                placed, page_of = [], []
                for li, ln in enumerate(lines):
                    if li == 0:
                        y += shape.get("above", 0)
                    if y + ln[2] > body_h and y > 0:
                        page, y = self.new_page(), 0.0
                    placed.append((ln[0], ln[1], ln[2], y, ln[4], ln[5], ln[6], self._advance(ln, shape)))
                    page_of.append(page)
                    y += self._advance(ln, shape)
                y += shape.get("below", 0)
                self.paragraph(p, shape, page_of, self.body_x, self.body_y, self.body_w, "body", lines=placed)
        else:
            prev_pgy = -1
            for p in self.doc.paragraphs:
                if p.para_shape is not None:
                    shape = para_shape(p.para_shape)
                lines = self.lines_for(p, shape, self.body_w)
                page_of = []
                for li, ln in enumerate(lines):
                    if self.flags:
                        brk = ln[6] & PAGE_FLAG and ln[6] & 1
                    else:  # 쪽 경계 표시가 없으면 세로 좌표가 줄어드는 곳(SO-SUEOP: 문단 '페이지 나눔' 플래그는 쪽 경계와 맞지 않았다)
                        brk = prev_pgy >= 0 and ln[3] < prev_pgy
                    if brk and not first:
                        page = self.new_page()
                    first = False
                    prev_pgy = ln[3]
                    page_of.append(page)
                self.paragraph(p, shape, page_of, x0=self.body_x, y0=self.body_y, width=self.body_w, where="body")
        for pg in self.pages:
            self.finish_page(pg)
        return self.pages

    def finish_page(self, page: Page) -> None:
        idx = self.pages.index(page)
        for pi, num in self.newnum_events:  # 새 번호로 시작: 그 쪽부터 번호를 다시 센다
            if pi <= idx:
                page.number = num + (idx - pi)
        for kind in ("header", "footer"):
            c = None
            for pi, k, ctrl in self.hf_events:
                if k == kind and pi <= idx:
                    c = ctrl
            if c is None:
                continue
            where = c.fields.get("where", 0)
            if where == 1 and page.number % 2 == 1 or where == 2 and page.number % 2 == 0:
                continue
            y0 = self.mt if kind == "header" else self.H - self.mb - self.fl
            for plist in c.children:
                self.paragraphs(plist, [page], self.body_x, y0, self.body_w, kind)
        if page.footnotes:
            self.draw_footnotes(page)
        setting = None
        for pi, pos, shp in self.pagenum_events:
            if pi <= idx:
                setting = (pos, shp)
        if setting and setting[0]:
            self.draw_page_number(page, *setting)

    def draw_page_number(self, page: Page, position: int, shape: int) -> None:
        """쪽 번호(특수 문자 20). 위치 1~3 위쪽 왼·가운데·오른쪽, 4~6 아래쪽 왼·가운데·오른쪽(메뉴 순서로 추정,
        german-legislative-system 위치 5 = 한컴 PDF 아래 가운데), 7·8 바깥쪽 위·아래."""
        n = page.number
        s = _roman(n) if shape % 3 == 1 else _roman(n).lower() if shape % 3 == 2 else str(n)
        if shape >= 3:
            s = f"- {s} -"
        size = 250
        top = position in (1, 2, 3, 7)
        col = {1: 0, 2: 1, 3: 2, 4: 0, 5: 1, 6: 2}.get(position, 2 if page.number % 2 else 0)
        y = (self.mt / 2 + size / 2) if top else (self.H - self.mb / 2 + size / 2)
        w = len(s) * size / 2
        x = self.body_x + [0, (self.body_w - w) / 2, self.body_w - w][col]
        page.svg.append(f'<text x="{x:.0f}" y="{y:.0f}" font-family="{SERIF_FAMILY}" font-size="{size}">{html.escape(s)}</text>')

    def draw_footnotes(self, page: Page) -> None:
        # 각주 줄의 저장 좌표 기준이 확인되지 않아, 본문 아래쪽에서 위로 쌓는다(근사).
        total = 0.0
        blocks = []
        for c in page.footnotes:
            h = sum(self._advance(ln, para_shape(None)) for plist in c.children for q in plist for ln in (q.lines or []))
            blocks.append((c, h))
            total += h
        y = self.body_bottom - total
        page.svg.append(f'<line x1="{self.body_x}" y1="{y - 60:.0f}" x2="{self.body_x + self.body_w / 3:.0f}" y2="{y - 60:.0f}" stroke="#000" stroke-width="5"/>')
        for c, h in blocks:
            for plist in c.children:
                self.paragraphs(plist, [page], self.body_x, y, self.body_w, "footnote", relative=False)
            y += h

    def _advance(self, ln, shape) -> float:
        """다음 줄까지 거리. 계산한 줄은 8번째 값에 들어 있다. 저장 줄은 줄 높이 × 줄 간격(글자만 있는 줄 기준)."""
        if len(ln) > 7:
            return ln[7]
        if shape.get("linesp_abs"):
            return shape["linesp"]
        return ln[2] * (shape.get("linesp") or 160) / 100

    # ---- 문단 ----
    def paragraphs(self, plist: list[Paragraph], pages: list[Page], x0: float, y0: float, width: float, where: str,
                   relative: bool = True) -> float:
        """문단 리스트(셀·머리말·각주 등)를 한 쪽에 그린다. 저장된 pgy가 있으면 y0 기준 상대 좌표로 쓴다."""
        shape = para_shape(None)
        y_run = y0
        for p in plist:
            if p.para_shape is not None:
                shape = para_shape(p.para_shape)
            if not relative or self.reflow:
                # 저장 좌표를 쓰지 않고 차례로 쌓는다(각주, 또는 위치가 저장되지 않은 문서)
                lines = self.lines_for(p, shape, width)
                base = lines[0][3]
                self.paragraph(p, shape, [pages[0]] * len(lines), x0, y_run - base, width, where, lines=lines)
                y_run += sum(self._advance(ln, shape) for ln in lines) + (shape.get("below", 0) if self.reflow else 0)
            else:
                self.paragraph(p, shape, [pages[0]] * max(1, len(p.lines)), x0, y0, width, where)
        return y_run

    def lines_for(self, p: Paragraph, shape: dict, width: float) -> list[tuple]:
        if not self.reflow:
            return p.lines or [(0, 0, char_shape(p.char_shape)["size"], 0, 0, 0, width)]
        key = (id(p), round(width))
        if key not in self._line_cache:
            self._line_cache[key] = self.break_lines(p, shape, width)
        return self._line_cache[key]

    def break_lines(self, p: Paragraph, shape: dict, width: float) -> list[tuple]:
        """줄 나눔 계산(위치가 저장되지 않은 문서용): 한글 97 폭 모델(영문 반각)로 어절 단위, 넘치면 글자 단위.

        돌려주는 줄 정보는 저장값과 같은 꼴(시작 위치, 공백 보정, 높이, 문단 위 기준 세로 좌표, 가로 시작, 0, 폭)이다.
        """
        runs = p.cshape_runs or [(0, p.char_shape)]
        run_shapes = [(pos, char_shape(b)) for pos, b in runs]
        default_h = run_shapes[0][1]["size"]
        left, right, indent = shape.get("left", 0), shape.get("right", 0), shape.get("indent", 0)
        toks = []  # (pos, w, h, is_space)
        pen = 0.0
        for pos, item in p.units:
            cs = self._shape_at(run_shapes, pos)
            if isinstance(item, str):
                w = self.glyph_width(item, cs)
                toks.append((pos, w, cs["size"], item == " "))
            elif isinstance(item, Table) or item.code == 11:
                f = item.fields
                if f.get("anchor", 0) == 0:
                    m = f["margin"][0]
                    toks.append((pos, (f.get("total_w") or f.get("box_w", 0)) + m[0] + m[1], (f.get("total_h") or f.get("box_h", 0)) + m[2] + m[3], False))
                else:
                    toks.append((pos, 0, 0, False))
            elif item.code == 9:
                w = item.fields.get("width", 0) or (1000 - (pen % 1000))
                toks.append((pos, w, 0, True))
            elif item.code in (18, 28):
                toks.append((pos, self.glyph_width("0", cs) * 2, cs["size"], False))
            else:
                toks.append((pos, 0, 0, False))
            pen += toks[-1][1]
        lines = []
        i, n = 0, len(toks)
        y = 0.0
        while True:
            first = not lines
            # 첫 줄은 왼쪽 여백 + 들여쓰기(음수면 내어쓰기), 다음 줄부터는 왼쪽 여백
            # (저장 배치 확인: hwp3-sample16 왼쪽 1750·들여쓰기 -500 → 첫 줄 시작 1250)
            off = max(0, left + indent) if first else left
            avail = max(1.0, width - right - off)
            j, w, last_space = i, 0.0, None
            while j < n:
                tw = toks[j][1]
                if w + tw > avail and j > i and not toks[j][3]:
                    break
                if toks[j][3]:
                    last_space = j
                w += tw
                j += 1
            if j < n and last_space is not None and last_space + 1 < j and last_space > i:
                j = last_space + 1  # 어절 경계에서 끊는다
            seg = toks[i:j] if j > i else toks[i:i + 1]
            j = max(j, i + 1) if n else 0
            text_w = sum(t[1] for t in seg)
            trail = 0.0
            for t in reversed(seg):
                if not t[3]:
                    break
                trail += t[1]
            text_w -= trail
            h = max([t[2] for t in seg] + [0]) or default_h
            # 글자 크기: 상자(표·그림)를 뺀 글자 중 가장 큰 것. 줄 간격은 '줄 높이 + (간격%-100%) × 글자 크기'
            # (저장 배치 문서에서 표가 든 줄의 다음 줄까지 거리로 확인: 05434 12290+0.6×275=12455)
            text_size = max([t[2] for t, u in zip(seg, p.units[i:j]) if isinstance(u[1], str)] + [0]) or default_h
            align = shape.get("align", 0)
            last = j >= n
            sx = off
            sadj = 0
            if align == 2:
                sx = off + max(0.0, avail - text_w)
            elif align == 3:
                sx = off + max(0.0, (avail - text_w) / 2)
            elif align in (0, 6) and not last:
                spaces = sum(1 for t in seg if t[3]) - (1 if seg and seg[-1][3] else 0)  # 줄 끝 공백 제외
                if spaces > 0 and avail > text_w:
                    sadj = int((avail - text_w) / spaces)
            start = seg[0][0] if seg else 0
            adv = shape["linesp"] if shape.get("linesp_abs") else h + ((shape.get("linesp") or 160) / 100 - 1) * text_size
            lines.append((start, sadj & 0xFFFF, h, y, sx, 0, width, adv))
            y += adv
            i = j
            if i >= n:
                break
        if not lines:
            lines.append((0, 0, default_h, 0.0, left, 0, width, default_h * (shape.get("linesp") or 160) / 100))
        return lines

    def paragraph(self, p: Paragraph, shape: dict, page_of: list[Page], x0: float, y0: float, width: float, where: str,
                  lines: list[tuple] | None = None) -> None:
        lines = lines or self.lines_for(p, shape, width)
        runs = p.cshape_runs or [(0, p.char_shape)]
        run_shapes = [(pos, char_shape(b)) for pos, b in runs]
        starts = [ln[0] for ln in lines] + [10 ** 9]
        units = p.units
        ui = 0
        for li, ln in enumerate(lines):
            page = page_of[min(li, len(page_of) - 1)]
            start, end = starts[li], starts[li + 1]
            seg = []
            while ui < len(units) and units[ui][0] < end:
                if units[ui][0] >= start:
                    seg.append(units[ui])
                ui += 1
            sadj = ln[1] - 65536 if ln[1] >= 32768 else ln[1]
            y = y0 + ln[3]
            h = ln[2]
            base = y + h * ASCENT
            x = x0 + ln[4]
            line_w = width if (ln[6] & PAGE_FLAG) or ln[6] == 0 else min(ln[6], width)
            right = x0 + line_w - shape.get("right", 0)
            last = li == len(lines) - 1
            self._tab_origin = x0
            self.line(page, seg, run_shapes, x, y, h, base, sadj, right, shape, last, where)

    def _shape_at(self, run_shapes, pos) -> dict:
        cur = run_shapes[0][1]
        for rp, cs in run_shapes:
            if rp <= pos:
                cur = cs
            else:
                break
        return cur

    def glyph_width(self, ch: str, cs: dict) -> float:
        lang = lang_of(ch)
        size = cs["size"]
        full = size * cs["ratio"][lang] / 100
        w = full * (0.5 if is_half(ch) else 1.0)
        return w + size * cs["spacing"][lang] / 100

    def line(self, page: Page, seg, run_shapes, x: float, y: float, h: float, base: float, sadj: int, right: float,
             shape: dict, last: bool, where: str) -> None:
        pen = x
        placed = []  # (x, ch, cs)
        for pos, item in seg:
            cs = self._shape_at(run_shapes, pos)
            if isinstance(item, str):
                ch = item
                if ch == " ":
                    placed.append((pen, " ", cs))  # 밑줄이 공백에도 이어지도록 자리를 남긴다
                    pen += self.glyph_width(" ", cs) + sadj
                    continue
                placed.append((pen, ch, cs))
                pen += self.glyph_width(ch, cs)
                continue
            c: Control = item
            if c.code == 9 or c.code == 24:  # 탭·하이픈: 저장된 폭(없으면 1000 hunit 간격의 다음 탭 자리)
                tw = c.fields.get("width", 0)
                if not tw and c.code == 9:
                    tw = 1000 - ((pen - self._tab_origin) % 1000)
                pen += tw
            elif c.code == 18:  # 자동 번호: 종류 0은 쪽 번호
                s = str(page.number) if c.fields.get("kind") == 0 else str(c.fields.get("number", ""))
                for ch in s:
                    placed.append((pen, ch, cs))
                    pen += self.glyph_width(ch, cs)
            elif c.code == 28:  # 개요 번호(규격 표 62): 저장된 단계별 번호·모양·장식 문자로 그린다
                for ch in outline_label(c):
                    placed.append((pen, ch, cs))
                    pen += self.glyph_width(ch, cs)
            elif c.code == 23:  # 겹침 글자
                t = c.fields.get("text", "")[:1]
                if t:
                    placed.append((pen, t, cs))
                pen += self.glyph_width("가", cs)
            elif isinstance(c, Table):
                pen += self.box(page, c, pen, y, where)
            elif c.code == 11:
                pen += self.picture(page, c, pen, y)
            elif c.code == 19 and c.fields.get("kind") == 0:  # 새 번호로 시작(쪽): 이 쪽의 번호를 바꾼다
                self.newnum_events.append((self.pages.index(page), c.fields.get("number", 1)))
            elif c.code == 20:  # 쪽번호달기: 이 쪽부터 쪽 번호를 그린다
                if where == "body":
                    self.pagenum_events.append((self.pages.index(page), c.fields.get("position", 0), c.fields.get("shape", 0)))
            elif c.code == 16:
                if where == "body":
                    self.hf_events.append((self.pages.index(page), c.fields.get("kind", "header"), c))
            elif c.code == 17:
                page.footnotes.append(c)
                s = str(c.fields.get("number", "")) + (")" if self.doc.docinfo.get("footnote_char") == ord(")") else "")
                for ch in s:
                    placed.append((pen, ch, dict(cs, size=cs["size"] * 0.6)))
                    pen += self.glyph_width(ch, cs) * 0.6
            elif c.code in (15, 5, 6, 7, 8, 20, 21, 22, 25, 26, 28, 30, 31):
                pass
            elif c.code == 14:
                self.hline(page, c, pen, y, h, x)
        # 양쪽 정렬: 마지막 줄이 아니면 오른쪽 끝을 맞춘다(저장 공백 보정은 한글 97 글꼴 기준이라 우리 폭과 다를 수 있음)
        align = shape.get("align", 0)
        if placed and align in (0, 6) and not last and pen > x:
            last_x, last_ch, last_cs = placed[-1]
            end = last_x + self.glyph_width(last_ch, last_cs)
            if end < right and (right - end) < (right - x) * 0.3 and len(placed) > 1:
                k = (right - x) / (end - x)
                placed = [(x + (px - x) * k, ch, cs) for px, ch, cs in placed]
        pitch = shape["linesp"] if shape.get("linesp_abs") else h * (shape.get("linesp") or 160) / 100
        self.emit_text(page, placed, base, y, max(pitch, h))
        text = "".join(ch for _, ch, _ in placed)
        page.lines.append(LineBox(page.number, x, y, h, base, text, where))

    def emit_text(self, page: Page, placed, base: float, top: float | None = None, pitch: float | None = None) -> None:
        """글자를 한글 97 자리(반각·전각)에 놓는다.

        - 전각 글자(한글·한자·기호): 글자마다 x를 준다. 장평은 가로 배율로.
        - 영문 등 반각 글자: 같은 모양이 이어지는 묶음을 Noto 글꼴로 자연스럽게 쓰고, 묶음 전체를
          한글 97 자리 폭에 맞게 가로 배율을 준다(한글 97은 영문을 반각 고정폭으로 계산했다).
        - 괘선: 글꼴의 전각 괘선을 반각 자리에 맞게 가로 0.5배.
        """
        i = 0
        while i < len(placed):
            cs = placed[i][2]
            kind = _kind(placed[i][1])
            j = i
            while j < len(placed) and placed[j][2] is cs and _kind(placed[j][1]) == kind and \
                    (kind != "ascii" or j == i or abs(placed[j][0] - (placed[j - 1][0] + self.glyph_width(placed[j - 1][1], cs))) < 1):
                j += 1
            group = placed[i:j]
            i = j
            lang = lang_of(group[0][1])
            fname = self._font_name(cs, lang)
            sans = any(k in fname.lower() for k in SANS_HINTS)
            fam = SANS_FAMILY if sans else SERIF_FAMILY
            size = cs["size"]
            attr = cs.get("attr", 0)
            dy = 0.0
            if attr & 0x20:
                size *= 0.6
                dy = -cs["size"] * 0.35
            elif attr & 0x40:
                size *= 0.6
                dy = cs["size"] * 0.15
            style = []
            if attr & 2:
                style.append('font-weight="700"')
            color = COLORS[cs.get("color", 0) & 7]
            if color != "#000000":
                style.append(f'fill="{color}"')
            st = " ".join(style)
            chars = "".join(html.escape(DISPLAY_SUBST.get(ch, ch), quote=False) for _, ch, _ in group)
            y = base + dy
            x0 = group[0][0]
            x1 = group[-1][0] + self.glyph_width(group[-1][1], cs)
            if kind == "space":  # 공백은 그리지 않고 밑줄만 잇는다
                pass
            elif kind == "ascii":
                metrics = ASCII_SANS if sans else ASCII_SERIF
                natural = sum(metrics.get(ch, 500) for _, ch, _ in group) / 1000 * size
                k = (x1 - x0) / natural if natural else 1
                page.svg.append(f'<text transform="translate({x0:.1f},{y:.1f}) scale({k:.3f},1)" x="0" y="0" '
                                f'font-family="{fam}" font-size="{size:.1f}" {st}>{chars}</text>')
            elif kind == "box" and top is not None:
                for px, ch, _ in group:
                    self.box_char(page, ch, px, px + self.glyph_width(ch, cs), top, top + pitch, base - cs["size"] * 0.35, cs["size"])
            else:
                ratio = cs["ratio"][lang] / 100 * (0.5 if kind == "box" else 1)
                if abs(ratio - 1) > 0.01:
                    xs = " ".join(f"{px / ratio:.1f}" for px, _, _ in group)
                    page.svg.append(f'<text transform="scale({ratio:.3f},1)" x="{xs}" y="{y:.1f}" font-family="{fam}" '
                                    f'font-size="{size:.1f}" {st}>{chars}</text>')
                else:
                    xs = " ".join(f"{px:.1f}" for px, _, _ in group)
                    page.svg.append(f'<text x="{xs}" y="{y:.1f}" font-family="{fam}" font-size="{size:.1f}" {st}>{chars}</text>')
            if attr & 1 and kind != "space":  # 기울임: 한글 글꼴에 이탤릭이 없어 기준선에서 기울인다
                last = page.svg.pop()
                page.svg.append(f'<g transform="translate({x0:.1f},{y:.1f}) skewX(-12) translate({-x0:.1f},{-y:.1f})">{last}</g>')
            if attr & 4:  # 밑줄은 선으로(SVG 변환기마다 text-decoration 지원이 달라서)
                uy = base + cs["size"] * 0.12
                page.svg.append(f'<line x1="{x0:.1f}" y1="{uy:.1f}" x2="{x1:.1f}" y2="{uy:.1f}" stroke="{color}" stroke-width="{max(5, cs["size"] / 20):.1f}"/>')

    def box_char(self, page: Page, ch: str, x0: float, x1: float, top: float, bottom: float, mid: float, size: float) -> None:
        """괘선 문자를 선으로 긋는다: 가로 팔은 글자 가운데 높이, 세로 팔은 줄 위에서 다음 줄 위까지(줄 사이가 이어진다)."""
        arms = box_arms(ch)
        if not arms:
            return
        cx = (x0 + x1) / 2
        lw = max(5.0, size / 28)
        gap = max(10.0, size / 9)
        out = []
        for d, wt in arms.items():
            sw = lw * (2.5 if wt == "heavy" else 1)
            offs = (-gap / 2, gap / 2) if wt == "double" else (0,)
            for o in offs:
                if d == "left":
                    out.append((x0, mid + o, cx, mid + o, sw))
                elif d == "right":
                    out.append((cx, mid + o, x1, mid + o, sw))
                elif d == "up":
                    out.append((cx + o, top, cx + o, mid, sw))
                elif d == "down":
                    out.append((cx + o, mid, cx + o, bottom, sw))
                elif d == "diag1":
                    out.append((x0, bottom, x1, top, sw))
                elif d == "diag2":
                    out.append((x0, top, x1, bottom, sw))
        page.svg.append("".join(f'<line x1="{a:.1f}" y1="{b:.1f}" x2="{c:.1f}" y2="{e:.1f}" stroke="#000" stroke-width="{w:.1f}"/>' for a, b, c, e, w in out))

    def _font_name(self, cs: dict, lang: int) -> str:
        try:
            return self.doc.fonts[lang][cs["font"][lang]]
        except (IndexError, KeyError):
            return ""

    # ---- 상자(표·글상자·버튼·수식) ----
    def box(self, page: Page, t: Table, pen: float, line_y: float, where: str) -> float:
        f = t.fields
        out_l, out_r, out_t, _ = f["margin"][0]
        if f.get("anchor", 0) == 0:  # 글자처럼 취급: 줄 안에 놓는다
            ox, oy = pen + out_l, line_y + out_t
            adv = f.get("total_w") or f.get("box_w", 0)
        else:
            # 문단·쪽·종이 기준: 한글 97이 계산해 저장한 박스 위치(box_x, box_y)를 본문 원점 기준으로 쓴다(근사)
            ox, oy = self.body_x + f.get("box_x", 0), self.body_y + f.get("box_y", 0)
            adv = 0
        cap_h = f.get("cap_h", 0) if t.caption and any(q.text().strip() for q in t.caption) else 0
        if cap_h and f.get("cap_pos") == 1:
            self.paragraphs(t.caption, [page], ox, oy, f.get("box_w", 0), "caption")
            oy += cap_h
        cm = f["margin"][2]  # 셀 여백 왼/오/위/아래
        for c in t.cells:
            cx, cy = ox + c.x, oy + c.y
            lt = c.raw.get("linetype", [1, 1, 1, 1])
            self._cell_border(page, cx, cy, c.w, c.h, lt, t.box_type, c.raw.get("shade", 0), c.raw.get("diagonal", 0))
            ix = cx + (cm[0] if t.box_type == "table" else f["margin"][1][0])
            iy = cy + (cm[2] if t.box_type == "table" else f["margin"][1][2])
            iw = c.w - (cm[0] + cm[1] if t.box_type == "table" else f["margin"][1][0] + f["margin"][1][1])
            if t.box_type == "equation":
                txt = " ".join(q.text() for q in c.paragraphs)
                page.svg.append(f'<text x="{ix:.0f}" y="{iy + 200:.0f}" font-family="{SERIF_FAMILY}" font-size="200" fill="#444">[수식] {html.escape(txt[:80])}</text>')
                continue
            vc = c.raw.get("vcenter", 0)
            content_h = c.raw.get("text_h", 0)
            if self.reflow and vc:  # 위치가 저장되지 않은 문서는 '텍스트 높이'도 0이라 계산한 줄로 잰다
                content_h = self.content_height(c.paragraphs, iw)
            if vc == 1 and content_h:
                iy = cy + (c.h - content_h) / 2
            elif vc == 2 and content_h:
                iy = cy + c.h - content_h - cm[3]
            self.paragraphs(c.paragraphs, [page], ix, iy, iw, "cell" if t.box_type == "table" else t.box_type)
        if cap_h and f.get("cap_pos") != 1:
            self.paragraphs(t.caption, [page], ox, oy + f.get("box_h", 0) + 100, f.get("box_w", 0), "caption")
        return adv + out_l + out_r

    def content_height(self, plist: list[Paragraph], width: float) -> float:
        shape = para_shape(None)
        total = 0.0
        for k, p in enumerate(plist):
            if p.para_shape is not None:
                shape = para_shape(p.para_shape)
            lines = self.lines_for(p, shape, width)
            total += sum(self._advance(ln, shape) for ln in lines[:-1]) + lines[-1][2] if k == len(plist) - 1 else \
                sum(self._advance(ln, shape) for ln in lines) + shape.get("below", 0)
        return total

    def _cell_border(self, page: Page, x, y, w, h, lt, box_type: str, shade: int = 0, diagonal: int = 0) -> None:
        """셀 테두리(규격 표 42): 0 투명, 1 실선, 2 굵은 실선, 3 점선, 4 2중 실선. 음영 비율·대각선도 그린다."""
        if shade:
            g = round(255 * (1 - min(shade, 100) / 100))
            page.svg.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w}" height="{h}" fill="rgb({g},{g},{g})"/>')
        if box_type in ("textbox", "button") and not any(lt):
            return
        sides = [((x, y), (x, y + h), (1, 0)), ((x + w, y), (x + w, y + h), (-1, 0)), ((x, y), (x + w, y), (0, 1)), ((x, y + h), (x + w, y + h), (0, -1))]
        for (a, b, (nx, ny)), k in zip(sides, lt):
            if not k:
                continue
            if k == 4:  # 2중: 바깥 선과 안쪽으로 20 들어간 선
                for off in (0, 20):
                    page.svg.append(f'<line x1="{a[0] + nx * off:.0f}" y1="{a[1] + ny * off:.0f}" x2="{b[0] + nx * off:.0f}" y2="{b[1] + ny * off:.0f}" stroke="#000" stroke-width="7"/>')
                continue
            sw = 30 if k == 2 else 7
            dash = ' stroke-dasharray="20 20"' if k == 3 else ""
            page.svg.append(f'<line x1="{a[0]:.0f}" y1="{a[1]:.0f}" x2="{b[0]:.0f}" y2="{b[1]:.0f}" stroke="#000" stroke-width="{sw}"{dash}/>')
        d = diagonal & 3
        if d in (1, 3):
            page.svg.append(f'<line x1="{x:.0f}" y1="{y:.0f}" x2="{x + w:.0f}" y2="{y + h:.0f}" stroke="#000" stroke-width="7"/>')
        if d in (2, 3):
            page.svg.append(f'<line x1="{x + w:.0f}" y1="{y:.0f}" x2="{x:.0f}" y2="{y + h:.0f}" stroke="#000" stroke-width="7"/>')

    def hline(self, page: Page, c: Control, pen: float, y: float, h: float, line_x: float) -> None:
        """선(특수 문자 14): 저장된 시작·끝 좌표(줄 기준)와 굵기로 그린다."""
        f = c.fields
        x1, y1, x2, y2 = f.get("line", [0, h / 2, 500, h / 2])
        ox = line_x if f.get("anchor", 0) == 0 else self.body_x
        sw = max(5, f.get("width", 6))
        page.svg.append(f'<line x1="{ox + x1:.0f}" y1="{y + y1:.0f}" x2="{ox + x2:.0f}" y2="{y + y2:.0f}" stroke="#000" stroke-width="{sw}"/>')

    # ---- 그림 ----
    def picture(self, page: Page, c: Control, pen: float, line_y: float) -> float:
        f = c.fields
        out_l, out_r, out_t, _ = f["margin"][0]
        if f.get("anchor", 0) == 0:
            ox, oy = pen + out_l, line_y + out_t
            adv = (f.get("total_w") or f.get("box_w", 0)) + 0
        else:
            ox, oy = self.body_x + f.get("box_x", 0), self.body_y + f.get("box_y", 0)
            adv = 0
        w, h = f.get("box_w", 0), f.get("box_h", 0)
        drawing = f.get("drawing") or {}
        if f.get("pictype") == 3 and drawing.get("items"):
            self.drawing(page, c, ox, oy)
        else:
            href = self._image_href(f)
            if href:
                page.svg.append(f'<image x="{ox:.0f}" y="{oy:.0f}" width="{w}" height="{h}" preserveAspectRatio="none" href="{href}"/>')
            else:
                page.svg.append(f'<rect x="{ox:.0f}" y="{oy:.0f}" width="{w}" height="{h}" fill="#f2f2f2" stroke="#999" stroke-width="6"/>'
                                f'<text x="{ox + 60:.0f}" y="{oy + 220:.0f}" font-size="160" fill="#666">[그림] {html.escape(f.get("path", "")[:40])}</text>')
        cap = c.children[0] if c.children else []
        if cap and any(q.text().strip() for q in cap):
            self.paragraphs(cap, [page], ox, oy + h + 100, w, "caption")
        return adv + out_l + out_r

    def _image_href(self, f: dict) -> str | None:
        name = f.get("path", "")
        if name in self.converted:
            return "data:image/png;base64," + base64.b64encode(self.converted[name]).decode()
        data = self.images.get(name)
        if data is None:
            return None
        if data[:2] == b"BM":
            try:
                import io

                from PIL import Image  # 선택: BMP는 브라우저 호환을 위해 PNG로 바꾼다

                buf = io.BytesIO()
                Image.open(io.BytesIO(data)).save(buf, "PNG")
                return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            except Exception:  # noqa: BLE001
                return "data:image/bmp;base64," + base64.b64encode(data).decode()
        mime = "image/gif" if data[:3] == b"GIF" else "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png" if data[:4] == b"\x89PNG" else None
        return f"data:{mime};base64," + base64.b64encode(data).decode() if mime else None

    def drawing(self, page: Page, c: Control, ox: float, oy: float) -> None:
        texts = c.children[1:]
        for o in c.fields["drawing"]["items"]:
            ax, ay = o["abs"]
            w, h = o["size"]
            x, y = ox + ax, oy + ay
            lc = o["line_color"]
            # 색 값의 상위 바이트가 0이 아니면 '없음'(투명)으로 본다(hwp3-sample19 글상자: 면 색 0x10000000, 한컴 PDF는 흰 바탕)
            stroke = "none" if lc >> 24 else f"#{lc & 0xFF:02x}{(lc >> 8) & 0xFF:02x}{(lc >> 16) & 0xFF:02x}"
            sw = max(5, o["line_width"])
            fc = o["fill_color"]
            fill = "none" if fc >> 24 else f"#{fc & 0xFF:02x}{(fc >> 8) & 0xFF:02x}{(fc >> 16) & 0xFF:02x}"
            t = o["type"]
            if t == 1:
                page.svg.append(f'<line x1="{x:.0f}" y1="{y:.0f}" x2="{x + w:.0f}" y2="{y + h:.0f}" stroke="{stroke}" stroke-width="{sw}"/>')
            elif t in (2, 6):
                page.svg.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w}" height="{h}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
            elif t in (3, 8):
                page.svg.append(f'<ellipse cx="{x + w / 2:.0f}" cy="{y + h / 2:.0f}" rx="{w / 2:.0f}" ry="{h / 2:.0f}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
            ti = o.get("text_index")
            if ti is not None and ti < len(texts):
                mx, my = o["text_margin"]
                self.paragraphs(texts[ti], [page], x + mx, y + my, max(0, w - 2 * mx), "textbox")

    # ---- SVG ----
    def svg(self, page: Page) -> str:
        mm_w, mm_h = self.W / 1800 * 25.4, self.H / 1800 * 25.4
        body = "\n".join(page.svg)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.W} {self.H}" width="{mm_w:.1f}mm" height="{mm_h:.1f}mm">'
                f'<rect width="{self.W}" height="{self.H}" fill="#fff"/>\n{body}\n</svg>')


def convert_images(images: dict[str, bytes], soffice: str) -> dict[str, bytes]:
    """WMF·EMF 포함 그림을 LibreOffice로 PNG로 바꾸고 흰 여백을 잘라 돌려준다(선택 기능, Pillow 필요).

    LibreOffice는 메타파일을 A4 한 장 위에 놓아 내보내므로 내용 영역만 잘라 낸다.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    try:
        from PIL import Image, ImageChops
    except ImportError:
        return {}
    metas = {k: v for k, v in images.items() if v[:4] in (b"\xd7\xcd\xc6\x9a", b"\x01\x00\x09\x00", b"\x02\x00\x09\x00") or v[40:44] == b" EMF"}
    if not metas:
        return {}
    out: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        names = {}
        for i, (k, v) in enumerate(metas.items()):
            ext = ".emf" if v[40:44] == b" EMF" else ".wmf"
            fn = tdp / f"img{i:04d}{ext}"
            fn.write_bytes(v)
            names[fn.stem] = k
        flt = 'png:draw_png_Export:{"PixelWidth":{"type":"long","value":"2480"},"PixelHeight":{"type":"long","value":"3508"}}'
        subprocess.run([soffice, f"-env:UserInstallation=file://{tdp / 'profile'}", "--headless", "--convert-to", flt,
                        "--outdir", str(tdp / "png"), *map(str, sorted(tdp.glob("img*")))], capture_output=True, timeout=1800, check=False)
        import io

        for png in sorted((tdp / "png").glob("*.png")):
            im = Image.open(png).convert("RGB")
            bbox = ImageChops.difference(im, Image.new("RGB", im.size, "white")).getbbox()
            if bbox:
                im = im.crop(bbox)
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=True)
            out[names[png.stem]] = buf.getvalue()
    return out


def render(doc: Document, soffice: str | None = None) -> tuple[list[Page], Renderer]:
    r = Renderer(doc)
    if soffice:
        r.converted = convert_images(doc.embedded_images, soffice)
    pages = r.run()
    return pages, r


def _font_faces(chars: set[str]) -> str:
    """Noto Serif/Sans KR에서 쓰인 글자만 뽑아 woff2로 넣은 @font-face. fontTools·brotli가 없으면 빈 문자열.

    글꼴 파일은 hwp3.fonts가 찾는다(HWP3_FONT_DIR → 패키지 fonts/ → 운영체제 글꼴 폴더).
    굵기 축이 있는 가변 글꼴은 한 파일로 100~900을 맡고, 아니면 보통(400)과 있으면 굵은(700) 파일을 따로 넣는다.
    """
    import io

    try:
        from fontTools import subset
    except ImportError:
        return ""
    from . import fonts as F

    def woff2(path) -> tuple[str, bool] | None:
        font = F.open_font(path)
        variable = "fvar" in font and any(a.axisTag == "wght" for a in font["fvar"].axes)
        opts = subset.Options()
        opts.flavor = "woff2"
        opts.layout_features = ["*"]
        sub = subset.Subsetter(opts)
        sub.populate(text="".join(chars))
        sub.subset(font)
        buf = io.BytesIO()
        try:
            font.flavor = "woff2"
            font.save(buf)
        except Exception:  # noqa: BLE001 - brotli 없음 등
            return None
        return base64.b64encode(buf.getvalue()).decode(), variable

    out = []
    for fam in F.FAMILIES:
        fs = F.find_font(fam)
        if fs is None:
            continue
        r = woff2(fs.regular.path)
        if r is None:
            return ""
        b64, variable = r
        weight = "100 900" if variable else "400"
        out.append(f"@font-face{{font-family:'{fam}';src:url(data:font/woff2;base64,{b64}) format('woff2');font-weight:{weight}}}")
        if not variable and fs.bold is not None:
            r = woff2(fs.bold.path)
            if r is not None:
                out.append(f"@font-face{{font-family:'{fam}';src:url(data:font/woff2;base64,{r[0]}) format('woff2');font-weight:700}}")
    return "".join(out)


def write_pages(doc: Document, out_dir, embed_fonts: bool = True, soffice: str | None = None) -> dict:
    """out_dir/pages/p0001.svg … 와 한 파일 보기 out_dir/pages/index.html(SVG를 안에 넣고, 가능하면 글꼴도 넣음)."""
    from pathlib import Path

    out = Path(out_dir) / "pages"
    out.mkdir(parents=True, exist_ok=True)
    pages, r = render(doc, soffice)
    svgs = []
    for i, pg in enumerate(pages, 1):
        svg = r.svg(pg)
        (out / f"p{i:04d}.svg").write_text(svg, encoding="utf-8")
        svgs.append(svg)
    chars = {ch for pg in pages for lb in pg.lines for ch in lb.text} | set("0123456789")
    faces = _font_faces(chars) if embed_fonts else ""
    body = "\n".join(f'<figure id="p{i}"><figcaption>{pg.number}</figcaption>{svg}</figure>' for i, (pg, svg) in enumerate(zip(pages, svgs), 1))
    title = html.escape((doc.summary.get("title") or "").strip() or "쪽 보기")
    (out / "index.html").write_text(
        f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title}</title>"
        f"<style>{faces}body{{background:#777;margin:0;padding:16px}}figure{{margin:0 auto 16px;max-width:210mm}}"
        "figure svg{width:100%;height:auto;background:#fff;box-shadow:0 1px 4px #0006;display:block}"
        "figcaption{color:#fff;font:12px sans-serif;margin:0 0 4px}</style>"
        f"{body}", encoding="utf-8")
    return {"pages": len(pages), "fonts_embedded": bool(faces), "images_converted": len(r.converted)}
