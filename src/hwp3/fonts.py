"""쪽 보기·PDF에 넣을 글꼴 파일 찾기.

한컴 글꼴 대신 Noto Serif KR·Noto Sans KR을 쓴다. 찾는 순서(앞에서 찾으면 뒤는 보지 않는다):

  1. 환경변수 HWP3_FONT_DIR (여러 폴더는 os.pathsep으로 구분)
  2. 패키지 안 fonts/ 폴더 (hwp3/fonts/)
  3. 운영체제 기본 글꼴 폴더: 맥 → 리눅스 → 윈도 순서로, 있는 폴더만

폴더는 하위 폴더까지 본다(리눅스는 /usr/share/fonts/opentype/noto/ 처럼 깊다).
파일 이름은 배포판마다 달라 다음을 모두 받는다. 앞쪽 이름을 먼저 고른다.

  NotoSerifKR[wght].ttf, NotoSerifKR-VF.ttf, NotoSerifKR-Regular.otf   (Google Fonts)
  NotoSerifCJKkr-Regular.otf                                            (Noto CJK 지역판)
  NotoSerifCJK-Regular.ttc                                              (Noto CJK 묶음. 한국어 면을 고른다)

표준 라이브러리만 쓴다. .ttc에서 한국어 면을 고를 때만 fontTools가 필요하다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ENV_VAR = "HWP3_FONT_DIR"
PACKAGE_FONT_DIR = Path(__file__).resolve().parent / "fonts"
EXTENSIONS = (".ttf", ".otf", ".ttc")

# 글꼴 가족 → 파일 이름 앞부분(우선순위 순)
FAMILIES: dict[str, tuple[str, ...]] = {
    "Noto Serif KR": ("NotoSerifKR", "NotoSerifCJKkr", "NotoSerifCJK"),
    "Noto Sans KR": ("NotoSansKR", "NotoSansCJKkr", "NotoSansCJK"),
}

_VARIABLE_HINT = re.compile(r"\[wght\]|(^|[-_ ])(vf|variable(font)?)($|[-_ .])", re.IGNORECASE)
_REGULAR = {"", "regular"}
_BOLD = {"bold"}


@dataclass(frozen=True)
class FontFile:
    family: str
    path: Path
    variable: bool  # 파일 이름으로 본 가변 글꼴 여부(굵기 축). 실제 여부는 열어서 fvar로 확인한다
    source: str  # env | package | system


@dataclass(frozen=True)
class FontSet:
    """한 가족의 보통 굵기 파일과, 가변 글꼴이 아닐 때 함께 쓸 굵은 파일(없으면 None)."""

    family: str
    regular: FontFile
    bold: FontFile | None = None


def system_font_dirs() -> list[Path]:
    home = Path(os.path.expanduser("~"))
    dirs = [
        # 맥
        home / "Library" / "Fonts",
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
        # 리눅스
        Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "fonts",
        home / ".fonts",
        Path("/usr/local/share/fonts"),
        Path("/usr/share/fonts"),
    ]
    # 윈도
    if os.environ.get("LOCALAPPDATA"):
        dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
    if os.environ.get("WINDIR"):
        dirs.append(Path(os.environ["WINDIR"]) / "Fonts")
    return dirs


def font_dirs() -> list[tuple[str, Path]]:
    """(출처, 폴더)를 찾는 순서대로. 없는 폴더도 그대로 낸다(진단용)."""
    out: list[tuple[str, Path]] = []
    for part in (os.environ.get(ENV_VAR) or "").split(os.pathsep):
        if part.strip():
            out.append(("env", Path(os.path.expanduser(part.strip()))))
    out.append(("package", PACKAGE_FONT_DIR))
    out += [("system", d) for d in system_font_dirs()]
    return out


def _classify(family: str, name: str) -> tuple[int, str, bool] | None:
    """파일 이름 → (이름 우선순위, 굵기 낱말, 가변 여부). 이 가족의 파일이 아니면 None."""
    stem, ext = os.path.splitext(name)
    if ext.lower() not in EXTENSIONS:
        return None
    for rank, prefix in enumerate(FAMILIES[family]):
        if not stem.lower().startswith(prefix.lower()):
            continue
        rest = stem[len(prefix):]
        if rest and rest[0].isalnum():  # NotoSansCJK 가 NotoSansCJKjp 를 잡지 않게, NotoSansKR 이 NotoSansKRx 를 잡지 않게
            continue
        variable = bool(_VARIABLE_HINT.search(rest))
        weight = re.sub(r"\[.*?\]|\.otf$|\.ttf$", "", rest, flags=re.IGNORECASE)
        weight = re.sub(r"(?i)(^|[-_ ])(vf|variable(font)?)(?=$|[-_ .])", "", weight).strip("-_ .").lower()
        return rank, weight, variable
    return None


@lru_cache(maxsize=64)
def _scan(directory: str) -> tuple[tuple[str, str], ...]:
    """폴더 아래 글꼴 파일 (이름, 전체 경로). 이름 순서로 고정해 결과가 실행마다 같게 한다."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for root, dirs, files in os.walk(directory, followlinks=True):
        real = os.path.realpath(root)
        if real in seen:  # 심볼릭 링크 고리
            dirs[:] = []
            continue
        seen.add(real)
        dirs.sort()
        for f in files:
            if f.lower().endswith(EXTENSIONS):
                found.append((f, os.path.join(root, f)))
    found.sort()
    return tuple(found)


def clear_cache() -> None:
    _scan.cache_clear()


def find_font(family: str) -> FontSet | None:
    """찾는 순서의 첫 폴더에서 이 가족의 글꼴을 고른다. 없으면 None."""
    if family not in FAMILIES:
        raise KeyError(family)
    for source, d in font_dirs():
        if not d.is_dir():
            continue
        regular: list[tuple[tuple, FontFile]] = []
        bold: list[tuple[tuple, FontFile]] = []
        for name, path in _scan(str(d)):
            c = _classify(family, name)
            if c is None:
                continue
            rank, weight, variable = c
            ff = FontFile(family, Path(path), variable, source)
            if variable or weight in _REGULAR:
                regular.append(((rank, 0 if variable else 1, name), ff))
            elif weight in _BOLD:
                bold.append(((rank, name), ff))
        if regular:
            key, best = min(regular, key=lambda x: x[0])
            b = None
            if not best.variable:
                same = [x for x in bold if x[0][0] == key[0]]  # 같은 이름 계열의 굵은 파일만
                b = min(same, key=lambda x: x[0])[1] if same else None
            return FontSet(family, best, b)
    return None


def find_fonts() -> dict[str, FontSet]:
    return {fam: fs for fam in FAMILIES if (fs := find_font(fam)) is not None}


def open_font(path: Path | str):
    """fontTools TTFont로 연다. .ttc는 이름에 KR·Korean이 든 면을 고르고, 없으면 첫 면을 쓴다."""
    from fontTools.ttLib import TTCollection, TTFont

    path = str(path)
    if not path.lower().endswith(".ttc"):
        return TTFont(path)
    index = 0
    coll = TTCollection(path, lazy=True)
    try:
        for i, f in enumerate(coll.fonts):
            names = {(f["name"].getDebugName(n) or "") for n in (1, 4, 6, 16)}
            if any(re.search(r"(?<![A-Za-z])KR(?![a-z])|Korean", s) for s in names):
                index = i
                break
    finally:
        coll.close()
    return TTFont(path, fontNumber=index)


def report() -> dict:
    """찾는 폴더와 고른 파일(명령행 `fonts`, 설치 점검용)."""
    return {
        "search_order": [{"source": s, "dir": str(d), "exists": d.is_dir()} for s, d in font_dirs()],
        "found": {
            fam: (None if (fs := find_font(fam)) is None else {
                "regular": str(fs.regular.path), "bold": str(fs.bold.path) if fs.bold else None,
                "variable_by_name": fs.regular.variable, "source": fs.regular.source,
            })
            for fam in FAMILIES
        },
    }
