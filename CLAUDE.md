# workspace-index — 작업공간 카탈로그

작업공간 루트(이 저장소의 부모 폴더) 아래 프로젝트 폴더를 카드·지도·규칙으로 설명하는 저장소다. 루트에서 연 세션은 `ORCHESTRATOR.md`를 따르는 오케스트레이터로 일하고, 이 저장소 안에서 연 세션은 아래 규칙으로 카탈로그 자체를 고친다.

## 넘지 않는 선
1. **프로젝트 폴더는 읽기만 한다.** 카탈로그 작업으로 작업공간의 다른 폴더에 파일을 만들거나 고치거나 지우지 않는다. 카드와 폴더가 어긋나면 카드를 고친다.
2. **비밀 자료는 열지 않는다.** `secret*`, `.env*`, `credentials/`, `private/`, `*.pem`은 이름만 카드에 적고 내용·값은 옮기지 않는다. 스크립트는 작업공간 파일 내용을 읽지 않는다.
3. **외부 패키지 금지.** 스크립트는 Python 3.9 표준 라이브러리와 bash만 쓴다.
4. **커밋은 이 저장소에만.** `python3 scripts/catalog.py build && python3 scripts/catalog.py check`가 종료 0이어야 커밋한다. 원격은 비공개로 둔다.
5. **`PROJECTS.md`는 손으로 고치지 않는다.** build로 다시 만든다.
6. 카드 `name`은 바꾸지 않는다(기억 은행 이름).

## 먼저 읽을 것
- 카드를 쓰거나 고칠 때: `docs/card-format.md`, `catalog/_template.md.txt`
- 루트 세션의 동작: `ORCHESTRATOR.md`
- `scripts/`를 고칠 때: `scripts/AGENTS.md`(불변식·테스트 지침). 고친 뒤 `python3 -m unittest scripts.test_catalog scripts.test_session_start`
- `memory/`를 고칠 때: `memory/README.md`. 고친 뒤 `python3 -m unittest memory.mac.test_hindsight_mem memory.mac.test_install`

## 커밋 메시지
- 카드 운영: `catalog: <name> <무엇을>` (예 `catalog: budget-book last_verified 갱신`)
- 스크립트·문서: `scripts: …`, `docs: …`, `memory: …`
