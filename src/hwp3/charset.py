"""HWP3 hchar(16비트) → 유니코드.

규격상 hchar 범위:
  0x0000~0x001F  특수 문자(제어). 파서가 따로 처리한다.
  0x0020~0x007F  ASCII
  0x8000~0xFFFF  한글(상용 조합형, 초·중·종성 5비트씩).
  그 밖          기호·한자 등 한글 자체 코드. symbols.py의 규칙으로 푼다.

해석 순서:
  1. 규칙(_rules): ASCII, 조합형 현대 한글·호환 자모(0x8000~0xD3FF, 파이썬 johab 코덱),
     symbols.lookup(문서 근거표 → KS 기호·한자·가나·괘선 계산식).
     0xD400 이상은 johab 코덱에 맡기지 않는다. KSSM 조합형에서는 그 자리가 기호·한자이지만
     HWP3(HNC)에서는 옛한글이라 코덱이 다른 글자를 내기 때문이다.
  2. 보충표(hnc_table.tsv): 한컴오피스 2014가 만든 HNC→유니코드 표. 한컴 사설 영역 값은
     KTUG 옛한글 표(public domain)·rhwp 한컴 기호 표(MIT)·한컴 PDF 확인값으로 바꿔 넣었다.
     규칙이 모르는 코드(2수준 한자, 라틴 확장, 옛한글, 한컴 전용 기호)를 채운다.
     글머리표(0x2F00~0x2F6F)는 계산식이 근사라서 보충표를 먼저 본다.
표에도 없는 코드(한컴 사설 영역 글자 등)는 None을 돌려주고 호출자가 집계한다.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from . import symbols

BULLET_RANGE = range(0x2F00, 0x2F70)
_TABLE_PATH = Path(__file__).with_name("hnc_table.tsv")
_use_table = True


@contextmanager
def without_hnc_table():
    """보충표를 끈 채 해석한다(근거표 검증처럼 '규칙이 모르는 자리'를 찾아야 할 때)."""
    global _use_table
    saved = _use_table
    _use_table = False
    decode_hchar.cache_clear()
    try:
        yield
    finally:
        _use_table = saved
        decode_hchar.cache_clear()


@lru_cache(maxsize=1)
def hnc_table() -> dict[int, str]:
    table: dict[int, str] = {}
    if not _TABLE_PATH.exists():
        return table
    for line in _TABLE_PATH.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        code, cps, *_ = line.split("\t")
        table[int(code, 16)] = "".join(chr(int(c, 16)) for c in cps.split())
    return table


def _rules(code: int) -> str | None:
    """규격·계산식·문서 근거로 정한 해석(보충표를 쓰지 않음)."""
    if 0x20 <= code < 0x80:
        return chr(code)
    if code >= 0x8000:
        if code >= 0xD400:
            return None
        try:
            s = bytes((code >> 8, code & 0xFF)).decode("johab")
        except UnicodeDecodeError:
            return None
        return s or None
    return symbols.lookup(code)


@lru_cache(maxsize=65536)
def decode_hchar(code: int) -> str | None:
    table = hnc_table() if _use_table else {}
    if code in BULLET_RANGE and table.get(code):
        return table[code]
    s = _rules(code)
    if s is not None:
        return s
    return table.get(code)


PUA_BASE = 0xF0000  # 해석 못한 코드를 잃지 않도록 보충 사설 영역에 코드값 그대로 둔다


def unknown_placeholder(code: int) -> str:
    return chr(PUA_BASE + code)


def unknown_code(ch: str) -> int | None:
    o = ord(ch)
    return o - PUA_BASE if PUA_BASE <= o < PUA_BASE + 0x10000 else None


def decode_hchars(codes, unknown: dict[int, int] | None = None) -> str:
    out = []
    for c in codes:
        s = decode_hchar(c)
        if s is None:
            if unknown is not None:
                unknown[c] = unknown.get(c, 0) + 1
            s = unknown_placeholder(c)
        out.append(s)
    return "".join(out)
