#!/bin/bash
# 1분 체험: 임시 폴더에 가짜 작업공간 두 개(회사 맥 main, 집 맥 home)를 만들고
# 예시 카드 6장으로 build → check, resolve → brief 를 돌려 본다. 실제 작업공간과 홈은 건드리지 않는다.
#
#   bash examples/demo.sh          # 끝나면 임시 폴더를 지운다
#   KEEP=1 bash examples/demo.sh   # 임시 폴더를 남겨 PROJECTS.md 등을 직접 열어 본다
set -eu

KIT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
[ "${KEEP:-}" = 1 ] || trap 'rm -rf "$TMP"' EXIT
TODAY=$(date +%F)

say() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
mk() { mkdir -p "$(dirname "$1")"; echo "demo" > "$1"; }

# 저장소 사본 + 예시 카드
setup_repo() {
    local repo="$1"
    mkdir -p "$repo"
    (cd "$KIT" && tar --exclude=.git --exclude=examples --exclude=__pycache__ -cf - .) | (cd "$repo" && tar -xf -)
    cp "$KIT"/examples/catalog/*.md "$repo/catalog/"
    cp "$KIT/examples/excluded.md" "$repo/excluded.md"
    cp "$KIT/examples/machines/home.md" "$repo/machines/home.md"
    # weather-bot 만 오래된 확인일(30일 경고 예시)로 두고 나머지는 오늘로
    for f in "$repo"/catalog/*.md; do
        case "$f" in *weather-bot.md) ;; *) sed -i.bak "s/^last_verified: .*/last_verified: $TODAY/" "$f" && rm -f "$f.bak" ;; esac
    done
}

# --- 회사 맥(기본 맥 main) ---
MAIN="$TMP/main/ai"
setup_repo "$MAIN/workspace-index"
mk "$MAIN/budget-book/CLAUDE.md"; mk "$MAIN/budget-book/README.md"; mk "$MAIN/budget-book/PLAN.md"
mk "$MAIN/cafe-booking/README.md"; mk "$MAIN/cafe-booking/docs/status.md"
mk "$MAIN/photo-sorter/README.md"; mk "$MAIN/photo-sorter_old/README.md"
mk "$MAIN/reading-log/README.md"; mk "$MAIN/weather-bot/README.md"
mk "$MAIN/work/client-crm/CLAUDE.md"; mkdir -p "$MAIN/work/client-crm/src" "$MAIN/work/client-crm/docs"
mk "$MAIN/scratch/notes.txt"

run() { (cd "$1" && WORKSPACE_INDEX_MACHINE="$2" python3 workspace-index/scripts/catalog.py "${@:3}"); }

say "회사 맥(main): build → check"
run "$MAIN" main build
run "$MAIN" main check || true

say "만들어진 지도 PROJECTS.md (앞부분)"
sed -n '1,20p' "$MAIN/workspace-index/PROJECTS.md"

say "키워드로 카드를 찾고(resolve) 위임 지시문을 뽑는다(brief)"
run "$MAIN" main resolve 가계부 대시보드
echo "→ '대시보드'는 카드 둘에 걸린다. 후보가 여럿이면 오케스트레이터는 고르게 한다."
run "$MAIN" main brief budget-book

say "카드 없는 새 폴더가 생기면 check 가 바로 잡아낸다"
mkdir "$MAIN/new-idea"
run "$MAIN" main check || echo "(종료 코드 $? — 카드를 만들거나 excluded.md 에 사유를 적으면 통과)"
rmdir "$MAIN/new-idea"

say "PROJECTS.md 를 손으로 고치면"
echo "| 손으로 넣은 줄 |" >> "$MAIN/workspace-index/PROJECTS.md"
run "$MAIN" main check | grep -E "PROJECTS|카드 " || true
run "$MAIN" main build >/dev/null

# --- 집 맥(home): 폴더 이름이 다르다 ---
HOME_WS="$TMP/home/ai"
mkdir -p "$HOME_WS"
cp -R "$MAIN/workspace-index" "$HOME_WS/workspace-index"
mk "$HOME_WS/gagyebu/CLAUDE.md"; mk "$HOME_WS/gagyebu/README.md"; mk "$HOME_WS/gagyebu/PLAN.md"; mk "$HOME_WS/photo-sorter/README.md"
mk "$HOME_WS/budget-old/README.md"; mk "$HOME_WS/downloads-tmp/a.txt"

say "집 맥(home): 같은 카드, 위치는 machines/home.md"
run "$HOME_WS" home check || true
if cmp -s "$MAIN/workspace-index/PROJECTS.md" "$HOME_WS/workspace-index/PROJECTS.md"; then
    echo "→ 두 맥의 PROJECTS.md 가 같은 바이트다(어느 맥에서 만들어도 같은 지도)."
fi

if [ "${KEEP:-}" = 1 ]; then
    say "임시 작업공간을 남겼다: $TMP"
fi
