# memory — 기억 저장소(Hindsight) 연결 (선택)

카드마다 기억 "은행"을 하나 두고, 프로젝트 폴더에서 세션을 열면 지난 세션이 남긴 **결정·함정·남은 일**이 바로 들어오게 하는 모듈이다. 카탈로그만 쓸 거라면 이 폴더는 설치하지 않아도 된다.

필요한 것: 직접 운영하는 서버 한 대(Docker), [Hindsight](https://github.com/vectorize-io/hindsight) 이미지, 임베딩용 OpenAI API 키, 도메인 하나. 맥 쪽은 Python 3.9 표준 라이브러리와 bash만 쓴다.

## 구성

```
맥 Claude Code 세션
  ├─ 세션 시작 훅  ~/.claude/hooks/hindsight-session-start.sh  → 은행 해석 → 프로젝트 요약 + 최근 기억 3건 + 안내문 주입
  ├─ 명령          ~/.claude/scripts/hindsight-mem (recall / retain / forget / recent / flush-queue / summary-sync)
  └─ 스킬          ~/.claude/skills/memory-save ("세션 종료"·"기억해"·"잊어줘"·"정정해")
        │ HTTPS + Bearer 공유키
        ▼
https://memory.example.com
        ▼ Caddy → hindsight:8888
서버 /opt/hindsight/docker-compose.yml (예시: server/docker-compose.example.yml)
  hindsight-api:1.0.0-slim · 임베딩 OpenAI · 재정렬 rrf · 내장 PostgreSQL(볼륨)
  백업: systemd hindsight-backup.timer 03:30 → /opt/hindsight/backups/<날짜>/<은행>.zip (14일)
        ▼ rsync (launchd 09:00, 맥이 켜져 있을 때)
~/Backups/hindsight/<날짜>/<은행>.zip (60일)
```

홈에 설치되는 것은 전부 이 폴더(`memory/mac/`)로의 **심볼릭 링크**다(launchd plist만 템플릿을 채워 복사). 원본을 고치면 바로 반영된다.

## 서버 올리기

1. 서버에 `/opt/hindsight/`를 만들고 `server/docker-compose.example.yml` → `docker-compose.yml`, `server/Caddyfile.example` → `Caddyfile`로 복사한다.
2. 같은 폴더에 `.env`(권한 600)를 만든다. 값은 어떤 문서·로그에도 적지 않는다.
   ```
   HINDSIGHT_API_TENANT_API_KEY=<충분히 긴 무작위 문자열. 맥들과 공유>
   OPENAI_API_KEY=<임베딩·사실 추출용>
   MEMORY_DOMAIN=memory.example.com
   ```
3. 도메인의 DNS를 서버로 향하게 하고 `docker compose up -d`. Caddy가 인증서를 받는다.
4. 백업(선택): `server/hindsight-backup.sh`를 `/opt/hindsight/`에, `hindsight-backup.{service,timer}`를 `/etc/systemd/system/`에 두고(`User=`를 서버 사용자로) `sudo systemctl enable --now hindsight-backup.timer`.
5. 확인: 맥에서 `python3 memory/scripts/smoke.py https://memory.example.com` (은행 `restore-test`에만 쓰고 지운다).

## 맥 설치

```
bash ~/ai/workspace-index/memory/mac/install.sh
```

컴퓨터마다 한 번씩 돌린다. 멱등이다. 하는 일:
- 링크 4개: `~/.claude/scripts/hindsight-mem`, 시작 훅, 스킬 `memory-save`, 백업 당김 스크립트
- `~/.claude/settings.json`에 SessionStart 훅 항목 추가(기존 파일은 백업 뒤 병합)
- `~/.claude/CLAUDE.md` 끝에 "## 기억 저장소 (Hindsight)" 절 추가(이미 있으면 건너뜀)
- launchd `io.github.workspace-index.hindsight-pull`(매일 09:00 백업 당김). 이름은 `HINDSIGHT_LAUNCHD_LABEL`로 바꿀 수 있다.

설정 파일 `~/.config/hindsight/env`(권한 600)는 직접 만든다.

```
HINDSIGHT_API_KEY=<서버 .env 의 HINDSIGHT_API_TENANT_API_KEY 와 같은 값>
HINDSIGHT_URL=https://memory.example.com
# 백업을 맥으로 당겨 오려면(없으면 건너뜀)
HINDSIGHT_BACKUP_REMOTE=user@server:/opt/hindsight/backups/
HINDSIGHT_SSH_KEY=~/.ssh/my-server-key
```

## 은행과 폴더

- 은행 하나 = 카드 하나. 은행 이름은 카드 `name`. 예약 이름은 `workspace-index`(루트 세션·이 저장소)와 `restore-test`(검증 전용).
- 세션이 열린 폴더 → 은행: 작업공간 루트와 `workspace-index/` 아래는 `workspace-index`; 이 맥 기준 카드 위치 아래면 그 카드(여러 개면 가장 깊은 경로); 비정본 폴더면 정본 카드; 그 밖(제외 폴더, 작업공간 밖)은 기억 없음.
- "이 맥 기준"은 `scripts/catalog.py`와 같은 규칙이다. `machines/<맥>.md` 위치표가 있으면 그것, 없으면 카드 `path`·`covers`. 같은 프로젝트면 컴퓨터마다 폴더 이름이 달라도 같은 은행이다.
- 은행은 첫 저장 때 자동으로 생긴다. 카드 목록 밖 이름은 명령이 거부한다(종료 2).
- `sensitive: pii` 카드의 은행이면 시작 안내에 "개인정보는 요약·가명으로도 저장하지 않는다"가 한 번 더 붙는다.

## 세션에서 쓰는 법

- **세션 시작**: 훅이 "# 기억 저장소 (Hindsight)" 절을 넣는다. 순서는 머리 줄(은행, 저장 토큰, 대기열 상태) → `## 프로젝트 요약 (자동 생성 · 갱신 YYYY-MM-DD HH:MM)`(이 맥의 현지 시각) + "언어모델이 기억에서 만든 요약이다. 최근 기억이나 프로젝트 파일과 어긋나면 그쪽을 따른다." 줄 + 요약 본문(4,000자 상한) → `## 최근 기억` 3건 → `## 사용법`. 요약이 없거나 비었거나 조회가 실패·시간 초과면 요약 절만 빠진다. 서버에 닿지 못하면 최근 기억 자리에 "닿지 못했습니다" 한 줄만 나오고 작업은 계속된다. 훅은 저장된 요약을 읽기만 하고 언어모델을 부르지 않는다. 훅 전체 상한 11초(토큰 발급 → 대기열 재전송 최대 6초 → 요약 조회 최대 3초, 최근 기억 몫 4초를 남기고 1초 미만이면 건너뜀 → 최근 기억 조회).
- **프로젝트 요약**: 은행마다 Hindsight mental model 하나(id `project-summary`, 이름 "프로젝트 요약"). 질문은 현재 상태·다음 할 일·함정이고 답은 한국어 마크다운(최대 800토큰). `trigger`는 `refresh_after_consolidation: true`, `mode: full`이라 서버가 새 기억을 통합한 뒤 처음부터 다시 쓴다. 대상 은행은 카드 name(removed 포함)과 `workspace-index`이며 `restore-test`는 제외한다. 질문·토큰 상한 같은 값은 `hindsight_mem.py` 머리의 `SUMMARY_*` 상수다.
- **"세션 종료"**: 진행 문서(PROGRESS·PLAN)를 정리한 뒤 `/memory-save`가 인계문(결정과 이유 / 함정과 배운 것 / 남은 일과 다음 첫 행동)을 저장한다. **파일이 정본이고 기억 저장소는 빨리 떠올리는 층이다.**
- **"기억해"** → `/memory-save note`. 설계를 바꾸는 결정·되돌리기 어려운 선택·재현하기 어려운 함정이 나오면 에이전트가 스스로 저장한다.
- **"잊어줘 <대상>"** → `/memory-save forget`(후보를 보여 주고 확인 뒤 삭제). **"정정해 <대상> → <새 내용>"** → `/memory-save correct`.
- 저장 금지: 실명, 주민번호, 급여, 연락처, 고객·후보자 정보, 토큰·키·비밀번호.
- **주 세션만 저장한다.** 저장·삭제에는 훅이 준 저장 토큰이 필요하고, 보조 에이전트는 토큰을 받지 않는다.
- 서버 불통·타임아웃·5xx로 저장이 실패하면 `~/.local/state/hindsight-queue/`에 쌓였다가 다음 세션 시작이나 다음 저장 때 자동 재전송된다.
- 직접 `retain`이 성공하면 그 은행에 요약이 없을 때 만든다(실패해도 저장은 성공, 표준 오류에 "경고: 프로젝트 요약을 만들지 못했습니다" 한 줄). 대기열 재전송에서는 만들지 않는다.
- `forget`이 성공하면 요약이 있을 때 비우고 처음부터 다시 갱신한다(clear → refresh). 지운 내용이 요약에 남지 않게 하려는 것이다. 이 단계가 실패하면 표준 출력에 "요약에 지운 내용이 남아 있을 수 있음(다음 갱신 때 사라짐)" 줄이 나오고 종료 코드는 0이다.

## 명령 (`~/.claude/scripts/hindsight-mem`)

| 부명령 | 쓰임 | 종료 코드 |
|---|---|---|
| `resolve-bank [cwd]` | 폴더 → 은행 이름 | 없으면 3 |
| `recent [--bank B] [--limit N]` | 최신순 원문 목록 | 실패 0 + stderr |
| `recall "<질의>" [--bank B]` | 의미 검색 | 실패 0 + stderr |
| `retain [파일] --kind handoff\|note\|correction\|retraction --token T [--bank B]` | 저장. 식별자 `<은행>-<시각>-<세션 앞 8자>` 출력. 직접 저장 성공 뒤 요약이 없으면 생성 | 토큰 불일치 4, 은행 거부 2, 실패 시 대기열 적재 0 |
| `forget <식별자> --token T [--bank B]` | 문서 삭제 뒤 요약 clear → refresh | 토큰 불일치 4, 삭제 실패 1, 요약 재생성 실패는 안내 줄 + 0 |
| `summary-sync --token T [--session S]` | 서버 은행 중 문서가 1건 이상인 요약 대상 은행마다 요약이 없으면 만든다. 은행별 `<은행>: 생성\|이미 있음\|실패 (…)`와 마지막 줄 `요약: 생성 N, 이미 있음 N, 실패 N` | 토큰 불일치 4, 은행 목록 실패·은행 하나라도 실패 1 |
| `flush-queue` | 대기열 재전송 | 0 |
| `session-start-context` | 훅용(stdin JSON) | 항상 0 |

조정값(환경변수): `HINDSIGHT_TIMEOUT`(조회 5초), `HINDSIGHT_RETAIN_TIMEOUT`(저장 30초), `HINDSIGHT_RECENT_SECONDS`(훅의 최근 기억 조회 4초), `HINDSIGHT_SUMMARY_SECONDS`(훅의 요약 조회 3초), `HINDSIGHT_SUMMARY_CHARS`(요약 본문 상한 4,000자), `HINDSIGHT_HOOK_SECONDS`(11초), `HINDSIGHT_FLUSH_MAX`/`HINDSIGHT_FLUSH_SECONDS`(3건/6초), `HINDSIGHT_BACKUP_KEEP_DAYS`/`HINDSIGHT_BACKUP_KEEP_MIN`(서버 14일/최근 7개), `HINDSIGHT_PULL_KEEP_DAYS`/`HINDSIGHT_PULL_KEEP_MIN`(맥 60일/최근 7개).

## 프로젝트 요약 비용·운영

- 요약 갱신(자동 갱신·`forget` 뒤 재생성·처음 생성) 한 번마다 서버 언어모델(`HINDSIGHT_API_LLM_PROVIDER` 설정의 모델)을 한 번 부른다. 저장이 잦은 은행일수록 늘어난다. 시작 훅은 비용을 만들지 않는다(저장된 본문 조회만).
- mental model API가 있는 Hindsight 버전(예시 compose의 `1.0.0-slim`)이 필요하다. 서버 쪽 추가 설정은 없다.
- 이미 기억이 있는 은행에 한꺼번에 만들려면 주 세션에서 `~/.claude/scripts/hindsight-mem summary-sync --token <저장 토큰>`. 멱등이다(이미 있으면 "이미 있음"). 만든 직후 본문은 비어 있고 서버가 수 초~수십 초 뒤 채운다.
- 시작 컨텍스트에서 요약 본문의 `#`~`###` 제목은 `####`로 낮춰 보인다(바깥 절과 섞이지 않게).

## 보안

- 키 하나로 모든 은행을 읽을 수 있다(은행별 권한 없음). 데이터 경로는 키 없으면 401이고 `/health`만 열려 있다. 예시 Caddyfile은 `/openapi.json`·`/docs`·`/redoc`을 404로 막는다.
- 키를 바꾸려면 서버 `.env`와 각 맥의 `~/.config/hindsight/env`를 같은 새 값으로 바꾸고 서버에서 `docker compose up -d hindsight`.

## 복원

은행 B를 복원하려면: ZIP을 컨테이너 안으로 복사 → 대상 은행이 **없어야** 하므로 필요하면 `DELETE /v1/default/banks/B`(되돌릴 수 없음) → `docker exec hindsight hindsight-admin import-bank --archive /tmp/B.zip --target-bank B`. 임베딩은 ZIP에 없어 가져올 때 다시 만든다(OpenAI 호출 발생).

## 검증

- 단위: `python3 -m unittest memory.mac.test_hindsight_mem memory.mac.test_install` (저장소 루트에서. 실제 홈·서버는 건드리지 않는다)
- 종단: `echo '{"cwd":"'"$HOME"'/ai/<카드 폴더>","session_id":"t"}' | ~/.claude/hooks/hindsight-session-start.sh`
