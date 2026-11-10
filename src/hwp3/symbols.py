# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# 이 파일의 코드 배치와 계산식은 LibreOffice hwpfilter(hwpfilter/source/hcode.cxx,
# MPL 2.0. 그 파일은 Apache License 2.0으로 받은 작업을 포함한다)를 읽고 옮긴 것이라
# 이 파일만 MPL 2.0을 따른다. 패키지의 나머지 코드는 MIT다(LICENSE).

"""한글 3.0 내부 코드의 비한글 영역 → 유니코드.

규격서(한글 문서 파일 형식 3.0, 2014 rev1.2)는 내부 코드표를 싣지 않고 "글 설명서"를 가리킨다.
규격서 안의 유일한 예시는 개요 불릿 '○ → 0x343b'이다. 아래 배치는 이 예시와
LibreOffice hwpfilter(hcode.cxx)의 변환 규칙을 대조해 계산식으로 옮긴 것이며,
KS X 1001 → 유니코드는 파이썬 cp949 코덱이 맡는다(코드표를 복사하지 않음).

  0x0080~0x00FF  0x81 “ 0x82 ” 0x83 ‘ 0x84 ’ (나머지는 아래 확인된 것만)
  0x1F00~0x1FBF  KS X 1001 10·11행(히라가나·가타카나), 행당 0x60
  0x2F00~0x2F6F  글머리표(하위 4비트 < 9)
  0x3013~0x305F  괘선: 11자(┌┬┐├┼┤└┴┘─│) × 7종
  0x3400~0x37BF  KS X 1001 기호 1~9행·12행, 행당 0x60 연속 배치(10·11행 자리는 12행)
  0x37C0~0x37C5  '한글과컴퓨터' 글자
  0x4000~0x5317  KS X 1001 한자 4888자 순번
"""

from __future__ import annotations

HCA_KSS = 0x3400
HCA_TG = 0x37C0
LINEBASE = 0x3013
N_HANJA = 4888

# 괘선 11자의 순서(LibreOffice index2dir: 10,11,9,14,15,13,6,7,5,3,12 = 방향 비트)
# 7종은 굵기·겹줄 조합이다. 0종(가는 선)·1종(굵은 선)·2종(겹줄)은 대응 문자가 분명하고,
# 3~6종(가는/굵은·겹줄 혼합)은 방향만 보존하는 근사 문자로 둔다.
_LINE_SETS = [
    "┌┬┐├┼┤└┴┘─│",
    "┏┳┓┣╋┫┗┻┛━┃",
    "╔╦╗╠╬╣╚╩╝═║",
    "┍┯┑┝┿┥┕┷┙━│",  # 근사: 가로 굵게
    "┎┰┒┠╂┨┖┸┚─┃",  # 근사: 세로 굵게
    "╒╤╕╞╪╡╘╧╛═│",  # 근사: 가로 겹줄
    "╓╥╖╟╫╢╙╨╜─║",  # 근사: 세로 겹줄
]
LINE_APPROX_GROUPS = {3, 4, 5, 6}

_QUOTES = {0x81: "“", 0x82: "”", 0x83: "‘", 0x84: "’"}
_HNC = {0x37C0: "한", 0x37C1: "글", 0x37C2: "과", 0x37C3: "컴", 0x37C4: "퓨", 0x37C5: "터"}

# 계산식 범위 밖에서 실제 문서에 나온 코드. 같은 문장이 들어 있는 다른 판(HWP5는 유니코드)과
# 대조해 확인한 것만 둔다. 유니코드 값과 같은 코드도 있지만
# (0x2024, 0x203B, 0x223C, 0x00B1, 0x00D7) 0x2051·0x2053·0x048A처럼 다른 것도 있어서
# '유니코드 그대로' 규칙으로 일반화하지 않는다.
EVIDENCE_MAP: dict[int, tuple[str, str]] = {
    0x00B1: ("±", "R오차 _1mm이내 — HWP5 2005H1 등 ±"),
    0x00D7: ("×", "정부고시노임단가_(1＋ — HWP5 1997H2 ×, 다른 HWP3 판 0x34xx ×"),
    0x2024: ("․", "조사_공표 — HWP5 판 U+2024(가장 많음)/U+30FB/U+00B7"),
    0x203B: ("※", "_ 위 식중 — HWP5 판 ※"),
    0x223C: ("∼", "1,000˚C_1,400˚C — HWP5 판 ∼"),
    0x2051: ("±", "R오차 _1mm이내 — HWP5 판 ±, 2003H2 HWP3 같은 자리 0x00B1"),
    0x2053: ("×", "상승율(%) = ──── _ 100 — HWP5 판 ×, 2003H2 HWP3 같은 자리 0x00D7"),
    0x048A: ("˚", "1,000_C∼1,400_C — HWP5 판 ˚(U+02DA)"),
    0x3066: ("·", "조사_공표하지 않은 — HWP5 판 가운뎃점 계열"),
    0x3157: ("·", "조사_공표하지 않은 — HWP5 판 가운뎃점 계열"),
    0x3068: ("’", "시행일자 : _98. 12. 30. — 연도 생략 부호, LibreOffice도 작은따옴표로 처리"),
}


def _ks(row: int, col: int) -> str | None:
    """KS X 1001 행·열(0xA1~0xFE) → 유니코드 한 글자."""
    if not (0xA1 <= row <= 0xFE and 0xA1 <= col <= 0xFE):
        return None
    try:
        s = bytes((row, col)).decode("cp949")
    except UnicodeDecodeError:
        return None
    return s if len(s) == 1 else None


def ks_symbol_code(code: int) -> tuple[int, int] | None:
    """0x3400~0x37BF → (KS 행 바이트, 열 바이트)."""
    if not (HCA_KSS <= code < HCA_TG):
        return None
    n = code - HCA_KSS
    row = n // 0x60 + 0xA1
    col = n % 0x60 + 0xA0
    if row == 0xAA:  # 10행 자리 → 12행(키릴)
        row += 2
    return row, col


def line_char(code: int) -> str | None:
    if LINEBASE <= code < LINEBASE + 11 * 7:
        g, i = divmod(code - LINEBASE, 11)
        return _LINE_SETS[g][i]
    return None


def lookup(code: int) -> str | None:
    if code in _QUOTES:
        return _QUOTES[code]
    if code in EVIDENCE_MAP:
        return EVIDENCE_MAP[code][0]
    if 0x4000 <= code < 0x8000:
        idx = code - 0x4000
        if idx >= N_HANJA:
            return None
        return _ks(idx // 94 + 0xCA, idx % 94 + 0xA1)
    if 0x1F00 <= code < 0x1FC0:
        lo = code & 0xFF
        row = 0xAA
        if lo >= 0x60:
            row += 1
            lo -= 0x60
        return _ks(row, lo + 0xA0)
    s = line_char(code)
    if s is not None:
        return s
    if 0x2F00 <= code <= 0x2F6F and (code & 0x0F) < 9:
        bases = [0xA1E0, 0xA1DB, 0xA1DE, 0xA1E2, 0xA1E4, 0xA2B7, 0xA2B9]
        base = bases[min((code - 0x2F00) >> 4, 6)]
        if (code & 0x0F) >= 6:
            base += 1
        return _ks(base >> 8, base & 0xFF)
    if code in _HNC:
        return _HNC[code]
    rc = ks_symbol_code(code)
    if rc is not None:
        return _ks(*rc)
    return None
