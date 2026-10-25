# scripts — 점검·빌드 스크립트

## 범위
`catalog.py`(check/build)와 `test_catalog.py`. 그리고 루트 세션 시작 훅 `session_start.py`와 `test_session_start.py`(아래 '시작 훅' 절). 카드·excluded.md·위치표(`machines/<맥>.md`)·작업공간 폴더 목록을 읽어 정합성을 검사하고, `last_activity` 줄과 `PROJECTS.md`를 생성한다. 카드 내용(요약·주의 절 등)을 판단하거나 고치는 일은 범위 밖이다. 상태(active/paused…) 판정도 범위 밖이다.

## 경계
- 작업공간(그 맥의 `~/ai`)에는 쓰지 않는다. 파일 내용도 읽지 않는다(이름·mtime·`git log` 날짜만).
- `secret`이라는 경로 성분은 어느 깊이에서도 열거하거나 진입하지 않는다.
- `catalog/*.md` 가운데 `last_activity:` 줄 외의 바이트는 바꾸지 않는다.
- `excluded.md`와 `machines/*.md`는 읽기만 한다. `ORCHESTRATOR.md`, `README.md`는 읽지 않는다.

## 불변식 (테스트가 지킨다)
- 저장소 루트 = 이 파일이 있는 `scripts/`의 부모, 작업공간 루트 = 그 부모, 맥 이름 = 작업공간 루트의 부모 폴더 이름(환경변수 `WORKSPACE_INDEX_MACHINE`이 있으면 그것). cwd 무관.
- 위치는 맥마다 다르다. 기본 맥(`config.json`의 `default_machine`, 또는 위치표가 없는 맥)은 카드 `path`·`covers`·`excluded.md`, 다른 맥은 `machines/<맥>.md`의 표 세 개. `path: -`는 기본 맥에 없음. 경로 존재·커버리지·entry_docs·read_scope·활동일은 **지금 맥 위치 기준**으로만 본다. 기본 맥이 아니면 entry_docs·read_scope 부재는 WARN.
- 경로 비교는 모두 NFC 정규화 후.
- 스캔 범위 = 지금 맥 루트 직하 + `bundle_dirs`(기본 `work`·`personal`)의 직하, 숨김·`secret`·이 저장소 제외. 각 폴더는 지금 맥의 세 역할(카드 위치 / 비정본 / 카드 없는 폴더) 중 정확히 하나.
- 같은 카드·excluded·위치표면 `PROJECTS.md`는 같은 바이트(`생성` 줄 제외). **어느 맥에서 만들어도** 같다. 그룹 표의 `맥` 열과 `## 맥별 위치` 절은 위치표만으로 정해지고 지금 맥에 기대지 않는다.
- `last_activity`는 두 맥 중 늦은 날짜: build는 이 맥 계산값이 저장값보다 뒤일 때만 쓰고, check도 그때만 `last_activity 저장값 X < 이 맥 계산값 Y (build 필요)` WARN.
- 구조 오류(문법·필수 필드·열거값·본문 제목, 위치표의 형식·없는 카드·한 폴더 두 역할·한 카드 두 경로·기본 맥 이름의 위치표, `path: -`인데 어느 위치표에도 없음)가 있으면 build는 아무것도 쓰지 않는다. 위치표 구조 오류는 어느 맥에서 돌려도 ERROR다.
- 종료코드: ERROR 있으면 1, 없으면 0. WARN은 영향 없음.
- removed 카드는 경로 존재 검사와 활동일 계산에서 면제, 저장값 유지.

## 구현 방식
- 표준 라이브러리만(`argparse`, `os`, `sys`, `re`, `subprocess`, `unicodedata`, `datetime`). Python 3.9 문법.
- 머리부 파서는 관대하지 않다. 부분집합 밖의 입력은 ERROR로 보고하고 고쳐 주지 않는다.
- 검사 항목을 더할 때는 ERROR/WARN 중 어느 쪽인지 먼저 정한다. 사용자가 고칠 수 있는 카드 결함은 ERROR, 정보성 어긋남(활동일 차이, 별칭 겹침, 30일 경과)은 WARN.
- 출력 줄 형식 `ERROR <카드|폴더|파일>: 설명` / `WARN <카드>: 설명` / 요약 `카드 N장, 오류 E, 경고 W`를 바꾸지 않는다(규칙 문서와 운영 절차가 이 형식을 안내한다). 위치표 오류의 `<파일>`은 `machines/<맥>.md`. build 성공 줄 끝의 ` (맥 <이름>)`도 운영 절차가 쓴다.
- 맥별 경로를 코드에 박지 않는다. 그룹·묶음 폴더·기본 맥 이름은 `config.json`(`catalog.py`의 `load_config`)에서 오고, 코드의 `GROUPS`·`BUNDLE_DIRS`·`DEFAULT_MACHINE`은 설정이 없을 때의 기본값이다. `session_start.py`도 같은 기본값과 `config.json`을 읽으니 함께 바꾼다. 설정 오류는 build·check 모두 `ERROR config.json: …`으로 멈춘다. 위치표 파서는 `memory/mac/hindsight_mem.py`에도 있으니(은행 해석) 형식을 바꾸면 그쪽과 테스트도 같이 바꾼다.

## 테스트 지침
- `python3 -m unittest scripts.test_catalog scripts.test_session_start` (저장소 루트에서). 테스트는 `tempfile`에 가짜 저장소(스크립트 복사본 + catalog + excluded.md)와 그 부모를 가짜 작업공간으로 만들어 subprocess로 실행한다. 실제 작업공간에 대한 테스트는 두지 않는다.
- 가짜 작업공간의 부모 폴더 이름은 무작위라 맥 이름에 맞는 위치표가 없다(기본 맥 규칙으로 돈다). 맥별 테스트는 `run_script(..., machine="other")`로 `WORKSPACE_INDEX_MACHINE`을 넘기고 `write_machine()`으로 `machines/other.md`를 쓴다. 도우미는 바깥 환경의 같은 변수를 지운다.
- 새 검사·새 출력은 반드시 테스트와 함께. 꼭 있어야 하는 케이스: 정상 입력 종료 0, 미등록 폴더, 문법 위반, pii에 read_scope 없음, PROJECTS.md 불일치→build 후 정상, build의 바이트 보존, NFD 폴더명 매칭, secret 미노출, `--no-coverage`. 맥별: 위치표로 build·check 통과(`(맥 other)`, `맥` 열, `맥별 위치` 표), 다른 맥의 미등록 폴더·사라진 비정본, 다른 맥 entry_docs 부재는 WARN, 위치표 구조 오류(없는 카드·두 역할·기본 맥 이름의 위치표)는 build까지 막음, `path: -` 카드, `last_activity` 늦은 값 유지, PROJECTS.md가 맥마다 같은 바이트. 설정: `config.json`의 그룹·묶음 폴더·기본 맥 반영, 잘못된 설정은 build·check 모두 멈춤.
- 실제 작업공간 확인은 테스트가 아니라 수동으로: 저장소 루트에서 `build` → `check` 종료 0, 전체 2초 안쪽.

## 시작 훅 (session_start.py)
- 루트 `.claude/settings.json`의 SessionStart 훅(matcher `startup|resume|clear|compact`)이 부른다. stdin 훅 JSON의 `cwd`가 작업공간 루트(`os.path.samefile`)일 때만 stdout에 낸다: 오케스트레이터 안내(실제 루트 경로 포함) → `## 이 맥` 절(지금 맥과 쓸 위치표, 기본 맥이면 카드 쪽, 위치표가 없으면 경고) → 동기화 한 줄 → `catalog.py check` 요약. 그 밖엔 무출력.
- 어떤 입력·실패에도 종료코드 0. 파일 내용은 읽지 않는다(위치표는 있는지만 보고, check를 subprocess로 부를 뿐). check는 20초 넘으면 건너뛴다.
- **쓰는 유일한 예외**: workspace-index를 `git fetch`하고, 추적 원격이 있고 작업트리가 깨끗하며 뒤처지기만 했을 때(앞선 커밋 없음, fetch 성공)만 `git merge --ff-only`. 그 밖엔 `원격보다 A개 앞섬, B개 뒤처짐`을 알리기만 한다. 커밋·reset·stash·push는 하지 않는다. git 명령마다 4초 제한, 추적 원격이 없으면 건너뛴다.
- 출력 상한: ERROR 30줄, WARN 15줄, 별칭 겹침 WARN은 개수만. 안내문은 ORCHESTRATOR.md 요약이며 어긋나면 ORCHESTRATOR.md를 기준으로 고친다.
- 테스트: `python3 -m unittest scripts.test_session_start`(가짜 작업공간 + 가짜 catalog.py, 기본값 `WORKSPACE_INDEX_MACHINE=other`). 이 맥 안내 세 갈래(위치표 있음·기본 맥·위치표 없음), git 저장소 아님 → 건너뜀, 가짜 bare 원격으로 깨끗+뒤처짐 → fast-forward, 작업트리 더러움 → 받지 않고 `0개 앞섬, 1개 뒤처짐`.
