#!/usr/bin/env python3
"""작업공간 루트 세션 시작 훅: 오케스트레이터 모드 안내, 이 맥의 위치표, 카탈로그 점검 결과를 컨텍스트로 낸다.

루트 .claude/settings.json 의 SessionStart 훅이 부른다. stdin은 훅 JSON(cwd 포함).
루트가 아닌 곳에서 열린 세션이면 아무것도 출력하지 않는다(ORCHESTRATOR.md 1절).
루트일 때는 다른 맥이 올린 카드를 받으려고 workspace-index 를 fetch 하고, 작업트리가 깨끗하고
뒤처지기만 했으면 fast-forward 한다. 그 밖의 파일은 쓰지 않는다.
어떤 경우에도 종료 코드 0. 표준 라이브러리만 쓴다(Python 3.9).
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
INDEX = os.path.dirname(HERE)
ROOT = os.path.dirname(INDEX)
MAX_ERROR = 30
MAX_WARN = 15
DEFAULT_MACHINE = "main"        # config.json default_machine 이 없을 때(scripts/catalog.py 와 같은 값)
GIT_TIMEOUT = 4

BRIEF = """# 오케스트레이터 모드 (workspace-index)
이 세션은 작업공간 루트(%(root)s)에서 열렸으므로 기본 역할은 오케스트레이터다. 규칙 정본은 workspace-index/ORCHESTRATOR.md, 지도는 PROJECTS.md(둘 다 이미 컨텍스트에 있다). 아래는 요약일 뿐이고 어긋나면 정본을 따른다.
- 요청마다 먼저 `python3 workspace-index/scripts/catalog.py resolve <키워드…>`로 후보 카드를 찾는다. 0개면 PROJECTS.md 요약 열을 보고 "혹시 이것인가요?"로 제안만 하고 확인을 받는다. 1개면 진행하되 요청과 안 맞아 보이면 묻고, 2개 이상이면 고르게 한다. 옛 이름으로 찾았으면 후속 카드로 안내하고 옛 폴더는 읽기 전용. 추측으로 폴더에 들어가지 않는다. 폴더 위치는 아래 '이 맥' 절이 말하는 곳에서 찾는다(resolve·brief도 이 맥 기준으로 경로를 낸다).
- 카드(workspace-index/catalog/<name>.md)를 열어 status·비정본 여부·sensitive·last_verified(30일)를 확인하고, entry_docs를 적힌 순서대로 읽는다.
- 프로젝트 폴더 안 코드 작업(구현·수정·검토·검증)은 직접 하지 않고 보조 에이전트에 넘긴다. 넘길 때 `python3 workspace-index/scripts/catalog.py brief <name>` 출력(절대 경로, 먼저 읽을 CLAUDE.md/AGENTS.md와 entry_docs, sensitive 제한, 주의 절)을 프롬프트에 붙이고 마지막 "작업 범위" 줄(할 것·안 할 것)을 채운다. 대체된 옛 폴더를 읽힐 때만 --read-only.
- 직접 고쳐도 되는 것은 workspace-index의 카드·excluded.md·build 산출물뿐이다. 고친 뒤 build → check → workspace-index에만 커밋하고 push.
- 라우팅에 필요한 확인(카드 읽기, last_verified가 30일 지난 카드의 ls·git log -1·entry_docs 확인)은 직접 한다. 여러 폴더를 훑는 대량 조사는 읽기 전용 보조 에이전트에 넘긴다.
- 사용자가 "직접 해줘"라고 하면 그 요청만 직접 처리한다. 그때도 민감 처리(4절)와 비밀 자료 금지는 그대로다."""


def default_machine():
    try:
        with open(os.path.join(INDEX, "config.json"), encoding="utf-8") as fh:
            value = json.load(fh).get("default_machine")
        return value if isinstance(value, str) and value else DEFAULT_MACHINE
    except Exception:
        return DEFAULT_MACHINE


def machine_name():
    env = os.environ.get("WORKSPACE_INDEX_MACHINE")
    if env:
        return env
    return os.path.basename(os.path.dirname(ROOT))


def machine_brief():
    name = machine_name()
    default = default_machine()
    table = os.path.join(INDEX, "machines", name + ".md")
    if os.path.isfile(table):
        return ("이 맥은 `%s`이다. 폴더 위치는 위치표 workspace-index/machines/%s.md를 쓴다"
                "(카드 path·covers·excluded.md는 기본 맥 %s 기준이라 이 맥에서는 쓰지 않는다). "
                "PROJECTS.md '맥' 열에 이 맥이 없는 카드는 이 맥에 폴더가 없으니 사용자에게 알린다."
                % (name, name, default))
    if name == default:
        return "이 맥은 기본 맥 `%s`이다. 카드 path·covers·excluded.md가 이 맥의 위치다." % name
    return ("이 맥(`%s`)의 위치표 workspace-index/machines/%s.md가 없다. 카드 path는 기본 맥 %s 기준이라 "
            "이 맥에서 맞지 않는다. 라우팅 전에 사용자에게 알리고 위치표부터 만든다."
            % (name, name, default))


def _git(*args):
    proc = subprocess.run(["git", "-C", INDEX] + list(args), capture_output=True, text=True,
                          timeout=GIT_TIMEOUT)
    return proc.returncode, proc.stdout.strip()


def sync_index():
    """다른 맥이 push 한 카드를 받는다. 깨끗하고 뒤처지기만 했으면 fast-forward. 결과 한 줄."""
    try:
        if _git("rev-parse", "--abbrev-ref", "@{u}")[0] != 0:
            return "workspace-index에 추적하는 원격 브랜치가 없어 동기화를 건너뜀."
        fetched = _git("fetch", "--quiet")[0] == 0
        code, out = _git("rev-list", "--left-right", "--count", "HEAD...@{u}")
        ahead, behind = (int(x) for x in out.split()) if code == 0 else (0, 0)
        dirty = bool(_git("status", "--porcelain")[1])
        note = "" if fetched else " (원격에 닿지 못해 마지막으로 받아 온 시점 기준)"
        if behind and not ahead and not dirty and fetched:
            if _git("merge", "--ff-only", "--quiet", "@{u}")[0] == 0:
                return "workspace-index: 다른 맥이 올린 커밋 %d개를 받아 왔다(fast-forward)." % behind
        if not ahead and not behind:
            return "workspace-index: 원격과 같다%s." % note
        msg = "workspace-index: 원격보다 %d개 앞섬, %d개 뒤처짐%s." % (ahead, behind, note)
        if behind:
            msg += " 카드를 고치기 전에 `git pull --ff-only`(또는 커밋 뒤 `git pull --rebase`)로 맞춘다."
        if ahead:
            msg += " 올리지 않은 커밋은 push 한다."
        return msg
    except Exception:
        return "workspace-index 동기화 확인 실패(건너뜀)."


def session_cwd():
    try:
        data = json.load(sys.stdin)
        cwd = data.get("cwd") or os.getcwd()
    except Exception:
        cwd = os.getcwd()
    if not isinstance(cwd, str):
        return ""
    return os.path.realpath(cwd)


def check_summary():
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "catalog.py"), "check"],
            cwd=ROOT, capture_output=True, text=True, timeout=20,
        )
    except subprocess.TimeoutExpired:
        return "check가 20초 안에 끝나지 않아 건너뜀. 필요하면 직접 돌린다."
    except Exception as exc:
        return "check 실행 실패: %s" % exc
    lines = [l for l in (proc.stdout + proc.stderr).splitlines() if l.strip()]
    errors = [l for l in lines if l.startswith("ERROR")]
    warns = [l for l in lines if l.startswith("WARN")]
    alias = [l for l in warns if "다른 카드와 겹침" in l]
    warns = [l for l in warns if l not in alias]
    summary = [l for l in lines if l.startswith("카드 ")]
    out = summary[-1:] or ["요약 줄 없음(종료 코드 %d)" % proc.returncode]
    out += errors[:MAX_ERROR]
    if len(errors) > MAX_ERROR:
        out.append("… ERROR %d줄 더 있음" % (len(errors) - MAX_ERROR))
    out += warns[:MAX_WARN]
    if len(warns) > MAX_WARN:
        out.append("… WARN %d줄 더 있음" % (len(warns) - MAX_WARN))
    if alias:
        out.append("(별칭 겹침 WARN %d줄은 생략. 라우팅 때 후보가 둘이면 사용자에게 고르게 한다.)" % len(alias))
    if errors:
        out.append("ERROR가 있으니 라우팅 전에 해당 카드부터 고친다(ORCHESTRATOR.md 7절).")
    return "\n".join(out)


def is_root(cwd):
    try:
        return os.path.samefile(cwd, ROOT)
    except OSError:
        return False


def main():
    if not is_root(session_cwd()):
        return
    print(BRIEF % {"root": ROOT})
    print()
    print("## 이 맥")
    print(machine_brief())
    print(sync_index())
    print()
    print("## 카탈로그 점검 (세션 시작 시점 catalog.py check)")
    print(check_summary())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
