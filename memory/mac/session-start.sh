#!/bin/bash
# Claude Code SessionStart 훅: stdin(훅 JSON)을 hindsight_mem.py session-start-context에 넘긴다.
# 홈에는 심볼릭 링크로 설치된다. 저장소 위치는 이 파일의 실제 경로로 찾는다.
# 어떤 경우에도 종료 코드 0. stderr는 ~/.local/state/hindsight/session-start.log에 덧붙인다.
set +e

self="$0"
real=$(python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$self" 2>/dev/null)
[ -n "$real" ] || real="$self"
here=$(dirname "$real")

export HINDSIGHT_FLUSH_SECONDS="${HINDSIGHT_FLUSH_SECONDS:-8}"
export HINDSIGHT_TIMEOUT="${HINDSIGHT_TIMEOUT:-5}"

log=/dev/null
if [ -n "${HOME:-}" ]; then
    log_dir="$HOME/.local/state/hindsight"
    mkdir -p "$log_dir" 2>/dev/null
    if touch "$log_dir/session-start.log" 2>/dev/null; then
        log="$log_dir/session-start.log"
    fi
fi

python3 "$here/hindsight_mem.py" session-start-context 2>>"$log" || true
exit 0
