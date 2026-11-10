"""HWP 3.x (한글 3.0/96/97) 문서 파서.

본 제품은 한글과컴퓨터의 한글 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.

공개된 "한글 3.0 문서 파일 형식" 규격에 따라 바이너리를 직접 읽는다.
외부 의존성 없이 표준 라이브러리(zlib, struct, codecs)만 쓴다.

    from hwp3 import parse_file
    doc = parse_file("x.hwp")
    for table in doc.iter_tables(): ...
"""

from .charset import decode_hchar, decode_hchars
from .model import (
    Cell,
    Control,
    Document,
    Paragraph,
    Table,
)
from .parser import HWP3Error, is_hwp3, parse_bytes, parse_file

__all__ = [
    "Cell",
    "Control",
    "Document",
    "HWP3Error",
    "Paragraph",
    "Table",
    "decode_hchar",
    "decode_hchars",
    "is_hwp3",
    "parse_bytes",
    "parse_file",
]
