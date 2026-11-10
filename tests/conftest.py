"""시험 공통 설정: 패키지를 설치하지 않았으면 ../src 에서 hwp3를 찾는다(설치했으면 설치본을 쓴다)."""

import importlib.util
import sys
from pathlib import Path

if importlib.util.find_spec("hwp3") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
