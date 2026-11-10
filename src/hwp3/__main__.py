"""명령행: HWP3 파일을 JSON·텍스트·HTML·CSV·엑셀로 변환.

    hwp3 convert <파일> -o <폴더> [--hancom] [--pages] [--xlsx [--xlsx-view raw] [--numbers]]
    hwp3 info <파일>
    hwp3 fonts

설치하지 않고 쓸 때는 `python -m hwp3 …`(hwp3 폴더의 위 폴더가 경로에 있어야 한다).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .export import to_json, write_all
from .parser import HWP3Error, parse_bytes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hwp3", description="HWP 3.x 문서 변환기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert", help="JSON·텍스트·HTML·표 CSV로 변환")
    c.add_argument("file")
    c.add_argument("-o", "--out", required=True)
    c.add_argument("--hancom", action="store_true", help="한컴식 보기(글상자→표, 빈칸 채우기 등)도 hancom/ 아래에 쓴다")
    c.add_argument("--pages", action="store_true", help="쪽 보기(쪽마다 SVG와 pages/index.html)도 쓴다")
    c.add_argument("--soffice", help="쪽 보기에서 WMF·EMF 그림을 PNG로 바꿀 LibreOffice soffice 경로(선택)")
    c.add_argument("--xlsx", action="store_true", help="엑셀 document.xlsx도 쓴다(openpyxl 필요). 본문·목록 시트와 표마다 시트 하나")
    c.add_argument("--xlsx-view", choices=("hancom", "raw"), default="hancom", help="엑셀 표 격자: hancom 한컴식(기본), raw 원본 그대로")
    c.add_argument("--numbers", action="store_true", help="엑셀에서 쉼표 숫자('56,352')를 숫자로 바꾼다(기본은 원문 글자 그대로)")
    i = sub.add_parser("info", help="구조 요약")
    i.add_argument("file")
    sub.add_parser("fonts", help="쪽 보기에 넣을 글꼴을 어디서 찾는지 보여준다(HWP3_FONT_DIR → 패키지 fonts/ → 운영체제 글꼴 폴더)")
    a = ap.parse_args(argv)
    if a.cmd == "fonts":
        from .fonts import report

        rep = report()
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0 if all(rep["found"].values()) else 1
    data = Path(a.file).read_bytes()
    try:
        doc = parse_bytes(data, path=a.file)
    except HWP3Error as e:
        print(f"HWP3 해석 실패: {e}", file=sys.stderr)
        return 2
    if a.cmd == "convert":
        r = write_all(doc, a.out, data)
        if a.hancom:
            from .compat import write_hancom

            r.update(write_hancom(doc, a.out))
        if a.pages:
            from .render import write_pages

            r.update(write_pages(doc, a.out, soffice=a.soffice))
        if a.xlsx:
            try:
                from .xlsx import write_xlsx

                r.update(write_xlsx(doc, Path(a.out) / "document.xlsx", view=a.xlsx_view, numbers=a.numbers))
            except ImportError:
                print("--xlsx에는 openpyxl이 필요하다: pip install openpyxl (또는 hwp3[xlsx])", file=sys.stderr)
                return 3
        print(json.dumps({"out": a.out, **r, "warnings": len(doc.warnings), "unknown_chars": sum(doc.unknown_chars.values())}, ensure_ascii=False))
    else:
        j = to_json(doc, data)
        j.pop("body")
        print(json.dumps(j, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
