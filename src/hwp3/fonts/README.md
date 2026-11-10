# 포함 글꼴

쪽 보기(`convert --pages`)가 한컴 글꼴 대신 사용하는 글꼴입니다. `hwp3.fonts`의 탐색 순서는 환경변수 `HWP3_FONT_DIR`, 이 폴더, 운영체제 글꼴 폴더입니다. 2026-11-10에 추가했습니다.

| 파일 | 글꼴 | 판 | 크기 | sha256 |
|---|---|---|---|---|
| `NotoSerifKR[wght].ttf` | Noto Serif KR, 굵기 200~900 가변 | 2.003-H1 | 23,795,420 | `11f8d5de6f1b79195efba3828aaa2ec95c1178f5ae976fb23c8d53250a9938f3` |
| `NotoSansKR[wght].ttf` | Noto Sans KR, 굵기 100~900 가변 | 2.004-H2 | 10,414,588 | `194018e6b2b293a7964f037b25c0249ce1418bc9ab3c971060a03aa57861e252` |

- 출처: `google/fonts` 저장소 `ofl/notoserifkr/`, `ofl/notosanskr/` (커밋 `5bb28733cec313b63fbd415b574b5ae35c8aa8b8`). 두 파일의 git blob 해시는 해당 커밋의 파일과 같습니다(`cc85b383…f492`, `b386890b…8808`). 수정하지 않은 원본입니다.
- 라이선스: SIL Open Font License 1.1. 전문은 `OFL-NotoSerifKR.txt`, `OFL-NotoSansKR.txt`(같은 커밋의 `OFL.txt`)에 있습니다. 글꼴을 다른 곳으로 옮길 때는 이 두 파일도 옮겨야 합니다.
- Noto Sans KR에는 예약 글꼴 이름 'Source'가 지정되어 있습니다. 쪽 보기는 문서에 쓰인 글자만 추린 글꼴을 HTML에 넣으며, 그 글꼴 이름은 'Noto Sans KR'이므로 예약 이름을 사용하지 않습니다.
- 글꼴을 바꾸려면 이 폴더의 파일을 교체하거나 `HWP3_FONT_DIR`로 다른 폴더를 지정합니다. 인식하는 파일 이름은 `fonts.py` 머리말에 있습니다.
