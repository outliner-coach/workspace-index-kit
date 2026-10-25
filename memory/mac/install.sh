#!/bin/bash
# Hindsight 기억 저장소 맥 설치(멱등). 홈 아래에만 쓴다. 셸 rc·PATH는 건드리지 않는다.
# 다시 돌려도 결과가 같다. 비밀값은 출력하지 않는다.
set -u

real_path() {
    python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$1"
}

REPO=$(cd "$(dirname "$(real_path "$0")")/../.." && pwd)
MAC="$REPO/memory/mac"
SETTINGS="$HOME/.claude/settings.json"
HOOK_CMD='$HOME/.claude/hooks/hindsight-session-start.sh'
FAILED=0
SUMMARY=()

note() { SUMMARY+=("$1"); }

# ensure_dir <경로> [권한]
ensure_dir() {
    local dir="$1" mode="${2:-}"
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir" || { echo "폴더를 만들지 못했습니다: $dir"; FAILED=1; return 1; }
    fi
    if [ -n "$mode" ]; then
        chmod "$mode" "$dir" || { FAILED=1; return 1; }
    fi
}

# link_file <저장소 안 원본> <홈 안 링크>
link_file() {
    local src="$1" dst="$2" cur
    if [ ! -e "$src" ]; then
        echo "원본이 없습니다: $src"; FAILED=1; return 1
    fi
    if [ -L "$dst" ]; then
        cur=$(readlink "$dst")
        if [ "$cur" = "$src" ]; then
            note "링크 유지: $dst"
            return 0
        fi
        echo "링크 대상 변경: $dst ($cur → $src)"
    elif [ -d "$dst" ]; then
        echo "링크 자리에 폴더가 있어 건너뜁니다: $dst"; FAILED=1; return 1
    elif [ -e "$dst" ]; then
        local bak="$dst.bak-$(date +%Y%m%d-%H%M%S)"
        mv "$dst" "$bak" || { FAILED=1; return 1; }
        echo "기존 파일을 옮기고 링크로 바꿉니다: $dst (보관 $bak)"
    fi
    ln -sfn "$src" "$dst" || { echo "링크를 만들지 못했습니다: $dst"; FAILED=1; return 1; }
    note "링크 설정: $dst → $src"
}

# add_hook <settings.json> <이벤트> <matcher> <command> <timeout>
# 같은 command가 이미 있으면 아무것도 쓰지 않는다. 쓰기 전에 .bak-<시각> 백업.
add_hook() {
    local result
    result=$(python3 - "$@" <<'PY'
import json, os, shutil, sys, time

path, event, matcher, command, timeout = sys.argv[1:6]
entry = {"matcher": matcher,
         "hooks": [{"type": "command", "command": command, "timeout": int(timeout)}]}

indent, trailing_nl, data = 2, True, None
if os.path.exists(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        print("ERROR settings.json을 JSON으로 읽지 못해 건드리지 않았습니다")
        sys.exit(1)
    trailing_nl = raw.endswith("\n")
    for line in raw.splitlines()[1:]:
        stripped = line.lstrip(" \t")
        if stripped and stripped != line:
            ws = line[:len(line) - len(stripped)]
            indent = "\t" if ws.startswith("\t") else len(ws)
            break
else:
    data = {}

if not isinstance(data, dict):
    print("ERROR settings.json 최상위가 객체가 아니라 건드리지 않았습니다")
    sys.exit(1)
hooks = data.setdefault("hooks", {})
if not isinstance(hooks, dict):
    print("ERROR settings.json의 hooks가 객체가 아니라 건드리지 않았습니다")
    sys.exit(1)
lst = hooks.setdefault(event, [])
if not isinstance(lst, list):
    print("ERROR settings.json의 hooks.%s가 배열이 아니라 건드리지 않았습니다" % event)
    sys.exit(1)
for e in lst:
    for h in (e.get("hooks") if isinstance(e, dict) else None) or []:
        if isinstance(h, dict) and h.get("command") == command:
            print("SKIP")
            sys.exit(0)
lst.append(entry)

backup = ""
if os.path.exists(path):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = "%s.bak-%s" % (path, stamp)
    n = 1
    while os.path.exists(backup):
        n += 1
        backup = "%s.bak-%s-%d" % (path, stamp, n)
    shutil.copy2(path, backup)

tmp = path + ".tmp-hindsight"
with open(tmp, "w", encoding="utf-8") as f:
    f.write(json.dumps(data, ensure_ascii=False, indent=indent))
    if trailing_nl:
        f.write("\n")
if os.path.exists(path):
    shutil.copymode(path, tmp)
os.replace(tmp, path)
print("ADDED " + backup if backup else "CREATED")
PY
)
    local rc=$?
    case "$result" in
        SKIP) note "훅 유지: $2 항목이 이미 있음" ;;
        CREATED) note "훅 추가: $1 새로 만듦 ($2)" ;;
        "ADDED "*) note "훅 추가: $2 (백업 ${result#ADDED })" ;;
        *) echo "${result#ERROR }"; FAILED=1 ;;
    esac
    [ $rc -eq 0 ] || FAILED=1
}

# check_env_file <경로>: 없으면 안내만, 있으면 권한 600 보장. 내용은 읽지 않는다.
check_env_file() {
    local f="$1" mode
    if [ ! -e "$f" ]; then
        echo "안내: $f 이 없습니다. HINDSIGHT_API_KEY·HINDSIGHT_URL(백업을 당겨 오려면 HINDSIGHT_BACKUP_REMOTE·HINDSIGHT_SSH_KEY도)을 넣어 권한 600으로 만들어 주세요."
        note "설정 파일: 없음(직접 만들어야 함)"
        return 0
    fi
    mode=$(python3 -c 'import os,sys;print(oct(os.stat(sys.argv[1]).st_mode & 0o777)[2:])' "$f")
    if [ "$mode" != "600" ]; then
        chmod 600 "$f" || { FAILED=1; return 1; }
        note "설정 파일: 권한 $mode → 600"
    else
        note "설정 파일: 있음(권한 600)"
    fi
}

append_claude_section() {
    # 홈 CLAUDE.md 끝에 기억 저장소 절을 한 번만 덧붙인다(제목 줄이 이미 있으면 건너뜀).
    local section="$1" target="$2" title
    title=$(head -1 "$section")
    if [ -e "$target" ] && grep -qF -- "$title" "$target"; then
        note "CLAUDE.md: 절 있음(변경 없음)"
        return 0
    fi
    { [ -e "$target" ] && [ -n "$(tail -c1 "$target")" ] && echo; echo; cat "$section"; } >> "$target" || { FAILED=1; return 1; }
    note "CLAUDE.md: 절 추가 ($target)"
}

install_launchd() {
    # 템플릿의 __HOME__·__LABEL__ 을 채워 ~/Library/LaunchAgents에 두고 적재한다(같은 내용이면 건너뜀).
    local template="$1" label="$2"
    local dst="$HOME/Library/LaunchAgents/$label.plist"
    ensure_dir "$HOME/Library/LaunchAgents"
    local src
    src=$(mktemp -t hindsight-plist) || { FAILED=1; return 1; }
    sed -e "s|__HOME__|$HOME|g" -e "s|__LABEL__|$label|g" "$template" > "$src" || { rm -f "$src"; FAILED=1; return 1; }
    # 실제 사용자 홈이 아닐 때(테스트의 임시 HOME)는 launchctl을 부르지 않는다.
    local real_home="/Users/$(id -un)"
    if [ -e "$dst" ] && cmp -s "$src" "$dst"; then
        note "launchd: $label 유지"
    else
        if [ -e "$dst" ] && [ "$HOME" = "$real_home" ]; then
            launchctl unload "$dst" >/dev/null 2>&1 || true
        fi
        cp "$src" "$dst" || { rm -f "$src"; FAILED=1; return 1; }
        note "launchd: $label 설치"
    fi
    rm -f "$src"
    if [ "$HOME" = "$real_home" ]; then
        launchctl load -w "$dst" >/dev/null 2>&1 || true
    fi
}

main() {
    ensure_dir "$HOME/.claude/scripts"
    ensure_dir "$HOME/.claude/hooks"
    ensure_dir "$HOME/.local/state/hindsight"
    ensure_dir "$HOME/.local/state/hindsight-queue"
    ensure_dir "$HOME/.config/hindsight" 700

    link_file "$MAC/hindsight_mem.py" "$HOME/.claude/scripts/hindsight-mem"
    link_file "$MAC/session-start.sh" "$HOME/.claude/hooks/hindsight-session-start.sh"

    add_hook "$SETTINGS" SessionStart "startup|resume|clear" "$HOOK_CMD" 15

    ensure_dir "$HOME/.claude/skills/memory-save"
    link_file "$MAC/skills/memory-save/SKILL.md" "$HOME/.claude/skills/memory-save/SKILL.md"
    append_claude_section "$MAC/claude-md-section.md" "$HOME/.claude/CLAUDE.md"

    link_file "$MAC/pull-backups.sh" "$HOME/.claude/scripts/hindsight-pull-backups"
    install_launchd "$MAC/hindsight-pull.plist.template" "${HINDSIGHT_LAUNCHD_LABEL:-io.github.workspace-index.hindsight-pull}"

    check_env_file "$HOME/.config/hindsight/env"

    echo "Hindsight 설치 결과 (저장소 $REPO)"
    local line
    for line in "${SUMMARY[@]+"${SUMMARY[@]}"}"; do
        echo "- $line"
    done
    if [ "$FAILED" -ne 0 ]; then
        echo "일부 단계가 실패했습니다. 위 메시지를 확인하세요."
        return 1
    fi
    echo "완료."
}

main
