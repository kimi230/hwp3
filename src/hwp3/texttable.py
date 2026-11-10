"""괘선 문자로 그린 '글자 표' 해석.

한글 2.x/3.x 시절 문서는 표 기능 대신 ┌─┬─┐ │ 같은 괘선 문자로 문단 안에 표를 그린 경우가 많다.
고정폭 배치에서 ASCII와 괘선(한글 내부 코드 0x3013~)은 1칸, 한글·한자·기호는 2칸을 차지한다
(실측: '┌────┬'의 가로줄 4개가 '│   1│'의 4칸과, 22칸 가로줄이 '갱'+공백18+'부'와 맞는다).
이 표시 폭으로 위치를 계산해 세로 괘선 위치에서 칸을 나눈다. 해석 결과는 줄마다 (시작 칸, 끝 칸, 글자) 목록이고,
의미(머리행/본문) 해석은 호출자가 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 세로 성분이 있는 괘선(칸 경계가 되는 문자)
VERTICAL = set("│┃║├┤┼┬┴┌┐└┘┏┓┗┛┣┫╋┳┻╔╗╚╝╠╣╬╦╩┍┑┕┙┝┥┿┯┷┎┒┖┚┠┨╂┰┸╒╕╘╛╞╡╪╤╧╓╖╙╜╟╢╫╥╨")
HORIZONTAL = set("─━═")
BOX = VERTICAL | HORIZONTAL
TOP_LEFT = set("┌┏╔┍┎╒╓")
BOTTOM_LEFT = set("└┗╚┕┖╘╙")
BOTTOM_RIGHT = set("┘┛╝┙┚╛╜")


def char_width(ch: str) -> int:
    o = ord(ch)
    return 1 if o < 0x80 or 0x2500 <= o <= 0x257F else 2


@dataclass
class Segment:
    x0: int  # 표시 칸(시작 포함)
    x1: int  # 표시 칸(끝 제외)
    text: str
    rule: bool  # 괘선·공백뿐인 조각(가로줄 일부)


@dataclass
class TextTableLine:
    para_index: int
    text: str
    is_rule: bool
    segments: list[Segment] = field(default_factory=list)
    separators: list[int] = field(default_factory=list)  # 세로 괘선의 표시 칸 위치


@dataclass
class TextTable:
    start: int  # 첫 줄의 문단 번호
    end: int  # 마지막 줄의 문단 번호(포함)
    lines: list[TextTableLine]


def split_line(text: str, para_index: int = -1) -> TextTableLine:
    pos = 0
    seps: list[int] = []
    segs: list[Segment] = []
    buf: list[str] = []
    start = 0
    for ch in text:
        w = char_width(ch)
        if ch in VERTICAL:
            if seps:
                body = "".join(buf)
                segs.append(Segment(start, pos, body.strip(), rule=all(c in BOX or c.isspace() for c in body)))
            seps.append(pos)
            buf = []
            start = pos + w
        else:
            buf.append(ch)
        pos += w
    tail = "".join(buf)
    if tail.strip():  # 오른쪽 테두리가 없는 줄의 마지막 칸
        segs.append(Segment(start, pos, tail.strip(), rule=all(c in BOX or c.isspace() for c in tail)))
    is_rule = all(c in BOX or c.isspace() for c in text) and any(c in BOX for c in text)
    return TextTableLine(para_index, text, is_rule, segs, seps)


def _starts_box(text: str) -> bool:
    s = text.lstrip()
    return bool(s) and s[0] in BOX


def _is_table_line(text: str) -> bool:
    """괘선으로 시작하거나, 세로 괘선으로 끝나는 줄(좌우 두 쪽에 걸친 표의 오른쪽 쪽은 '      │' 같은 줄이 낀다)."""
    s = text.rstrip()
    return _starts_box(s) or (bool(s) and s[-1] in VERTICAL)


def find_text_tables(lines: list[str]) -> list[TextTable]:
    """문단 글자 목록에서 괘선 표 구간을 묶는다.

    괘선으로 시작하는 줄에서 시작해 괘선 줄이 이어지는 동안 계속한다. └ 계열로 시작하는 줄, ┘ 계열로
    끝나는 가로 괘선 줄에서 닫고, 중간에 ┌ 계열 줄이 오면 그 앞에서 나눈다. 두 쪽에 걸쳐 그린 표의
    오른쪽 쪽은 ┌ 대신 ┬ 등으로 시작한다.
    """
    out: list[TextTable] = []
    i = 0
    n = len(lines)
    while i < n:
        if _starts_box(lines[i]):
            j = i
            block = []
            closed = False
            while j < n and _is_table_line(lines[j]):
                if j > i and lines[j].lstrip()[:1] in TOP_LEFT:
                    break  # 새 표의 왼쪽 위 모서리
                block.append(split_line(lines[j], j))
                st = lines[j].strip()
                if st[:1] in BOTTOM_LEFT or (block[-1].is_rule and st[-1:] in BOTTOM_RIGHT):
                    closed = True
                    break
                j += 1
            end = j if closed else j - 1
            if block:
                out.append(TextTable(i, end, block))
            i = end + 1
        else:
            i += 1
    return out
