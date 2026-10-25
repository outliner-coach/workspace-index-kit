# 기여하기

버그 제보·제안은 이슈로 남겨 주세요. **이슈와 PR은 공개입니다.** 실제 카드, 폴더 이름, 서버 주소, 키, 개인정보를 붙이지 말고 `examples/`의 가상 카드로 재현해 주세요.

## 원칙
- 외부 패키지 없이 Python 3.9 표준 라이브러리와 bash만 씁니다.
- 스크립트는 작업공간 파일의 **내용을 읽지 않습니다**(이름·수정 시각·git 날짜만). `secret` 경로는 어느 깊이에서도 열거하지 않습니다.
- 출력 줄 형식 `ERROR <대상>: 설명` / `WARN <대상>: 설명` / `카드 N장, 오류 E, 경고 W`는 규칙 문서와 시작 훅이 기대하는 계약이라 바꾸지 않습니다.
- 새 검사·새 출력은 테스트와 함께 넣습니다. 테스트는 임시 폴더의 가짜 작업공간·가짜 홈에서만 돕니다. 자세한 불변식은 `scripts/AGENTS.md`.

## 확인
```bash
python3 -m unittest scripts.test_catalog scripts.test_session_start memory.mac.test_hindsight_mem memory.mac.test_install
bash examples/demo.sh
```
