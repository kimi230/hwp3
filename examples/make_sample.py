"""예시 파일 sample.hwp를 만든다 / Builds the example file sample.hwp.

규격(한글 문서 파일 형식 3.0 rev1.2)의 표대로 바이트를 직접 조립한다. 내용은 모두 지어낸 것이다.
The bytes are assembled by hand from the published HWP 3.0 format tables. All content is made up.

    python examples/make_sample.py            # examples/sample.hwp
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

from hwp3 import decode_hchar, parse_bytes
from hwp3.parser import WSIZE

SIG = b"HWP Document File V3.00 \x1a\x01\x02\x03\x04\x05"
END_PARA = struct.pack("<BHHBBIB", 0, 0, 0, 0, 0, 0, 0) + bytes(31)
_LINE_CODES = {decode_hchar(code): code for code in range(0x3013, 0x3013 + 77)}


def hstr(s: str) -> list[int]:
    """글자열 → 한글 내부 코드(hchar) 목록. 한글은 조합형, 괘선 문자는 한글 괘선 코드."""
    out = []
    for ch in s:
        if ord(ch) < 0x80:
            out.append(ord(ch))
        elif 0x2500 <= ord(ch) <= 0x257F:
            out.append(_LINE_CODES[ch])
        else:
            b = ch.encode("johab")
            out.append((b[0] << 8) | b[1])
    return out


def para(chars: list[bytes | int]) -> bytes:
    """문단 하나. chars의 int는 일반 글자, bytes는 미리 만든 특수 문자(표, 각주)."""
    body = b""
    nch = 0
    for c in chars:
        if isinstance(c, int):
            body += struct.pack("<H", c)
            nch += 1
        else:
            body += c
            nch += WSIZE[struct.unpack_from("<H", c, 0)[0]]
    body += struct.pack("<H", 13)
    head = struct.pack("<BHHBBIB", 0, nch + 1, 1, 0, 0, 0, 0) + bytes(31) + bytes(187)
    line = struct.pack("<HHH6sH", 0, 0, 0, bytes(6), 0)
    return head + line + body


def plist(*paras: bytes) -> bytes:
    return b"".join(paras) + END_PARA


def footnote(text: str) -> bytes:
    return struct.pack("<HIH", 17, 0, 17) + bytes(8) + struct.pack("<HHH", 0, 0, 1000) + plist(para(hstr(text)))


def table(cells: list[dict]) -> bytes:
    """표(특수 문자 10). cells: x, y, w, h(hunit, 1800 = 1인치), text, lines(왼·오·위·아래 선 종류), shade(%), vcenter."""
    info = bytes(8) + struct.pack("<BBhhHH", 0, 0, 0, 0, 0, 10) + bytes(24)
    w = max(c["x"] + c["w"] for c in cells)
    h = max(c["y"] + c["h"] for c in cells)
    info += struct.pack("<HH", w, h) + bytes(6) + struct.pack("<HH", w, h) + bytes(4) + b"\x00\x00" + bytes(4) + bytes(4)
    info += struct.pack("<HH", 0, 0) + bytes(2) + struct.pack("<HHHH", 0, 0, len(cells), 0)
    cinfo = b""
    for i, c in enumerate(cells):
        cinfo += struct.pack("<BBHHHHHHH", 0, i, 0, c["x"], c["y"], c["w"], c["h"], 0, 0) + bytes(2)
        cinfo += bytes([1, c.get("vcenter", 1)]) + bytes(c.get("lines", [1, 1, 1, 1])) + bytes([c.get("shade", 0), 0, 0])
    lists = b"".join(plist(para(hstr(c["text"]))) for c in cells)
    return struct.pack("<HIH", 10, 0, 10) + info + cinfo + lists + plist()


def build(paragraphs: bytes, title: str) -> bytes:
    docinfo = bytearray(128)
    docinfo[124] = 1  # 본문 압축
    summary = bytearray(1008)
    t = hstr(title)
    struct.pack_into(f"<{len(t)}H", summary, 0, *t)
    fonts = b"".join(struct.pack("<H", 1) + b"Font".ljust(40, b"\0") for _ in range(7))
    styles = struct.pack("<H", 1) + b"Normal".ljust(20, b"\0") + bytes(31) + bytes(187)
    body = fonts + styles + paragraphs + bytes(8)
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    stream = co.compress(body) + co.flush() + struct.pack("<II", zlib.crc32(body), len(body))
    return SIG + bytes(docinfo) + bytes(summary) + stream + struct.pack("<II", 0x80000000, 0)


def cell(x, y, w, h, text, **kw) -> dict:
    return {"x": x, "y": y, "w": w, "h": h, "text": text, **kw}


HEAD = {"shade": 15, "lines": [1, 1, 2, 1]}  # 머리 칸: 음영 15%, 위쪽 굵은 선
# 5행 3열. '품목'은 세로 병합, '1분기'와 '합계'는 가로 병합
QUOTE = [
    cell(0, 0, 1800, 800, "품목", **HEAD),
    cell(1800, 0, 3600, 400, "1분기", **HEAD),
    cell(1800, 400, 1800, 400, "수량", shade=15),
    cell(3600, 400, 1800, 400, "금액", shade=15),
    cell(0, 800, 1800, 400, "사과"),
    cell(1800, 800, 1800, 400, "120"),
    cell(3600, 800, 1800, 400, "360,000"),
    cell(0, 1200, 1800, 400, "배"),
    cell(1800, 1200, 1800, 400, "45"),
    cell(3600, 1200, 1800, 400, "225,000"),
    cell(0, 1600, 3600, 400, "합계", lines=[1, 1, 3, 2]),
    cell(3600, 1600, 1800, 400, "585,000", lines=[1, 1, 3, 2]),
]

# 괘선 문자로 그린 옛날식 표(표시 폭: 괘선·영문 1칸, 한글 2칸)
TEXT_TABLE = [
    "┌────┬────────┐",
    "│구분│금액    │",
    "├────┼────────┤",
    "│배송│   3,000│",
    "│포장│   1,500│",
    "└────┴────────┘",
]


def sample() -> bytes:
    return build(
        plist(
            para(hstr("과일 가게 견적서 (예시)")),
            para(hstr("아래 숫자는 hwp3 예시를 위해 지어낸 것이다.") + [footnote("실제 가격이 아니다.")]),
            para([table(QUOTE)]),
            para(hstr("부대 비용")),
            *[para(hstr(s)) for s in TEXT_TABLE],
            para(hstr("끝.")),
        ),
        title="hwp3 예시",
    )


def main() -> int:
    data = sample()
    doc = parse_bytes(data)
    assert doc.warnings == [] and doc.body_crc_ok is True
    out = Path(__file__).resolve().parent / "sample.hwp"
    out.write_bytes(data)
    print(f"{out} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
