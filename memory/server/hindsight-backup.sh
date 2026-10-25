#!/bin/bash
# Hindsight 은행별 매일 내보내기(서버에서 실행, systemd hindsight-backup.timer가 매일 03:30에 부른다).
# 은행 목록은 루프백 API(GET /v1/default/banks), 내보내기는 컨테이너 안 `hindsight-admin export-bank`.
# 결과: /opt/hindsight/backups/<YYYY-MM-DD>/<은행>.zip, 한 줄 로그 backup.log.
# 키는 /opt/hindsight/.env 의 HINDSIGHT_API_TENANT_API_KEY에서 읽어 헤더에만 쓴다(출력 금지).
# 종료: 0 성공(은행 없음 포함), 1 실패한 은행이 있거나 목록을 못 받음.
set -u

ENV_FILE="${HINDSIGHT_ENV_FILE:-/opt/hindsight/.env}"
ROOT="${HINDSIGHT_BACKUP_ROOT:-/opt/hindsight/backups}"
KEEP_DAYS="${HINDSIGHT_BACKUP_KEEP_DAYS:-14}"
CONTAINER="${HINDSIGHT_CONTAINER:-hindsight}"
API="${HINDSIGHT_API_URL:-http://127.0.0.1:8888}"
TODAY=$(date +%F)
DEST="$ROOT/$TODAY"
LOG="$ROOT/backup.log"

mkdir -p "$ROOT" || exit 1

log() {
    echo "$(date '+%F %T %z') $1" >> "$LOG"
    echo "$1"
}

if [ ! -r "$ENV_FILE" ]; then
    log "실패: env 파일을 읽을 수 없음 ($ENV_FILE)"
    exit 1
fi

# 키 줄 하나만 뽑는다(env 파일을 셸로 실행하지 않는다 — 다른 값에 $·공백·따옴표가 있어도 안전). echo 금지.
HINDSIGHT_API_TENANT_API_KEY=$(sed -n 's/^HINDSIGHT_API_TENANT_API_KEY=//p' "$ENV_FILE" | head -1 | sed -e "s/^['\"]//" -e "s/['\"]\$//")
export HINDSIGHT_API_TENANT_API_KEY
if [ -z "$HINDSIGHT_API_TENANT_API_KEY" ]; then
    log "실패: HINDSIGHT_API_TENANT_API_KEY 없음"
    exit 1
fi

BANKS=$(HINDSIGHT_API_URL="$API" python3 - <<'PY'
import json, os, sys, urllib.request
base = os.environ["HINDSIGHT_API_URL"] + "/v1/default/banks"
offset, seen = 0, 0
while True:
    req = urllib.request.Request("%s?limit=100&offset=%d" % (base, offset))
    req.add_header("Authorization", "Bearer " + os.environ["HINDSIGHT_API_TENANT_API_KEY"])
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print("ERR %s" % type(e).__name__, file=sys.stderr)
        sys.exit(1)
    items = data.get("banks", []) if isinstance(data, dict) else data
    for b in items or []:
        bid = (b.get("bank_id") or b.get("id")) if isinstance(b, dict) else b
        if bid:
            print(bid)
    seen += len(items or [])
    total = data.get("total") if isinstance(data, dict) else None
    if not items or not isinstance(total, int) or seen >= total:
        break
    offset = seen
PY
)
rc=$?
unset HINDSIGHT_API_TENANT_API_KEY
if [ $rc -ne 0 ]; then
    log "실패: 은행 목록을 받지 못함"
    exit 1
fi

# 오래된 날짜 폴더 정리는 오늘 내보내기가 성공한 뒤에만 한다(아래 cleanup_old). 실패가 이어져도 지난 백업은 남는다.
cleanup_old() {
    local cutoff name d
    cutoff=$(date -d "-$KEEP_DAYS days" +%F)
    # 최근 KEEP_MIN개 폴더는 날짜와 무관하게 항상 남긴다.
    local keep_min="${HINDSIGHT_BACKUP_KEEP_MIN:-7}"
    local total
    total=$(ls -d "$ROOT"/????-??-?? 2>/dev/null | wc -l)
    for d in $(ls -d "$ROOT"/????-??-?? 2>/dev/null | sort); do
        [ -d "$d" ] || continue
        [ "$total" -gt "$keep_min" ] || break
        name=$(basename "$d")
        if [[ "$name" < "$cutoff" ]]; then
            rm -rf -- "$d"
            total=$((total-1))
        fi
    done
}

if [ -z "$BANKS" ]; then
    log "은행 없음"
    exit 0
fi

mkdir -p "$DEST" || { log "실패: 폴더 생성 $DEST"; exit 1; }
ok=0; fail=0; failed=()
while IFS= read -r bank; do
    [ -n "$bank" ] || continue
    case "$bank" in
        *[!A-Za-z0-9._-]*) failed+=("$bank(이름)"); fail=$((fail+1)); continue ;;
    esac
    tmp="/tmp/hs-backup-$bank.zip"
    if docker exec "$CONTAINER" hindsight-admin export-bank --bank "$bank" --output "$tmp" >/dev/null 2>&1 \
        && docker cp "$CONTAINER:$tmp" "$DEST/$bank.zip.part" >/dev/null 2>&1 \
        && python3 -c 'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); sys.exit(0 if "manifest.json" in z.namelist() and z.testzip() is None else 1)' "$DEST/$bank.zip.part" \
        && mv -f "$DEST/$bank.zip.part" "$DEST/$bank.zip"; then
        ok=$((ok+1))
    else
        rm -f "$DEST/$bank.zip.part"
        failed+=("$bank"); fail=$((fail+1))
    fi
    docker exec "$CONTAINER" rm -f "$tmp" >/dev/null 2>&1
done <<< "$BANKS"

if [ $fail -gt 0 ]; then
    log "부분 실패: $TODAY 성공 ${ok}, 실패 ${fail} (${failed[*]})"
    exit 1
fi
cleanup_old
log "성공: $TODAY 은행 ${ok}개 → $DEST"
exit 0
