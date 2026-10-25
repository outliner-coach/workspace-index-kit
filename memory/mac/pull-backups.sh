#!/bin/bash
# 서버의 Hindsight 백업 폴더를 맥으로 당겨 온다(launchd 가 매일 09:00 실행).
# 서버 <HINDSIGHT_BACKUP_REMOTE> → ~/Backups/hindsight/ (rsync, --delete 없음: 서버에서 지워져도 맥 사본은 남기고 60일 기한·최근 7개 보존으로만 정리).
# 설정은 환경변수 또는 ~/.config/hindsight/env 의 HINDSIGHT_BACKUP_REMOTE(예 user@host:/srv/hindsight/backups/)와
# HINDSIGHT_SSH_KEY(키 파일 경로). 키는 경로만 쓰고 내용은 출력하지 않는다. REMOTE 가 없으면 건너뛴다.
set -u

ENV_FILE="${HINDSIGHT_ENV_FILE:-$HOME/.config/hindsight/env}"
env_value() {
    # env 파일에서 한 키의 값을 읽는다(따옴표 제거). 없으면 빈 문자열.
    [ -r "$ENV_FILE" ] || return 0
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" | tail -1 | sed -e 's/^["'\'']//' -e 's/["'\'']$//'
}

REMOTE="${HINDSIGHT_BACKUP_REMOTE:-$(env_value HINDSIGHT_BACKUP_REMOTE)}"
SSH_KEY="${HINDSIGHT_SSH_KEY:-$(env_value HINDSIGHT_SSH_KEY)}"
SSH_KEY="${SSH_KEY/#\~/$HOME}"
DEST="${HINDSIGHT_BACKUP_DEST:-$HOME/Backups/hindsight/}"
STATE_DIR="$HOME/.local/state/hindsight"
LOG="$STATE_DIR/pull-backups.log"

mkdir -p "$DEST" "$STATE_DIR" || exit 1

log() {
    echo "$(date '+%F %T %z') $1" >> "$LOG"
    echo "$1"
}

if [ -z "$REMOTE" ]; then
    log "건너뜀: HINDSIGHT_BACKUP_REMOTE 설정 없음"
    exit 0
fi
ssh_cmd="ssh -o BatchMode=yes -o ConnectTimeout=20"
if [ -n "$SSH_KEY" ]; then
    if [ ! -r "$SSH_KEY" ]; then
        log "실패: SSH 키 파일을 읽을 수 없음"
        exit 1
    fi
    ssh_cmd="ssh -i \"$SSH_KEY\" -o BatchMode=yes -o ConnectTimeout=20"
fi

err=$(rsync -a -e "$ssh_cmd" "$REMOTE" "$DEST" 2>&1)
rc=$?
if [ $rc -ne 0 ]; then
    log "실패: rsync 종료 $rc ($(printf '%s' "$err" | tail -1))"
    exit 1
fi
# 서버를 그대로 따라가지 않고(--delete 없음) 맥은 자체 기한(기본 60일)으로만 정리한다.
keep_days="${HINDSIGHT_PULL_KEEP_DAYS:-60}"
keep_min="${HINDSIGHT_PULL_KEEP_MIN:-7}"
total=$(ls -d "$DEST"/????-??-?? 2>/dev/null | wc -l | tr -d ' ')
for d in $(find "$DEST" -maxdepth 1 -type d -name '????-??-??' -mtime +"$keep_days" 2>/dev/null | sort); do
    [ "$total" -gt "$keep_min" ] || break
    rm -rf -- "$d" && total=$((total-1))
done
latest=$(ls -1d "$DEST"/????-??-?? 2>/dev/null | tail -1)
count=0
[ -n "$latest" ] && count=$(ls -1 "$latest"/*.zip 2>/dev/null | wc -l | tr -d ' ')
log "성공: 최신 $(basename "${latest:-없음}") ZIP ${count}개 → $DEST"
exit 0
