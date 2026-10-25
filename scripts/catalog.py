#!/usr/bin/env python3
"""작업공간 프로젝트 카탈로그 점검·빌드 스크립트.

    python3 scripts/catalog.py check [--no-coverage]   # 읽기 전용 검증
    python3 scripts/catalog.py build                    # last_activity 갱신 + PROJECTS.md 생성

- Python 3.9, 표준 라이브러리만 사용한다. 그룹·묶음 폴더·기본 맥 이름은 저장소 루트의 config.json 으로 정한다.
- 저장소 루트는 이 파일의 위치(scripts/..)로 찾고, 작업공간 루트는 그 부모다. cwd 에 의존하지 않는다.
- 맥이 여럿일 수 있다. 카드 path·covers·excluded.md 는 기본 맥(default_machine) 기준이고, 다른 맥은 machines/<맥>.md 위치표를 쓴다.
  지금 맥 이름은 작업공간 루트의 부모 폴더 이름(/Users/<맥>/ai)이다.
- 파일 내용은 절대 읽지 않는다(카드·excluded.md·PROJECTS.md 제외). 폴더는 이름·mtime·git 날짜만 본다.
- `secret` 이름의 경로는 어떤 경우에도 열거하거나 열지 않는다.
- 경로 비교는 모두 NFC 정규화 후에 한다(macOS 는 한글 폴더명을 NFD 로 돌려준다).
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import unicodedata

# ---------------------------------------------------------------------------
# 상수 (규칙은 docs/card-format.md)
# ---------------------------------------------------------------------------

FIELDS = [
    "name", "path", "group", "status", "summary", "stack", "entry_docs",
    "covers", "related", "aliases", "sensitive", "read_scope",
    "last_activity", "last_verified",
]
LIST_FIELDS = {"stack", "entry_docs", "covers", "related", "aliases", "read_scope"}
SCALAR_FIELDS = set(FIELDS) - LIST_FIELDS
DATE_FIELDS = {"last_activity", "last_verified"}

STATUSES = ["active", "paused", "done", "superseded", "template", "scratch", "removed"]
STATUS_ORDER = {s: i for i, s in enumerate(STATUSES)}
GROUPS = ["업무", "사이드 프로젝트", "생활", "학습·실험"]   # config.json 의 groups 로 바꾼다
SENSITIVES = ["none", "credentials", "pii"]
HEADINGS = ["## 한눈에", "## 들어갈 때 읽을 것", "## 주의", "## 관련 폴더"]

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$")

SECRET = "secret"                       # 절대 열지 않는 이름
SKIP_TOP = {SECRET, "workspace-index"}  # 스캔 범위에서 빼는 루트 폴더
BUNDLE_DIRS = ["work", "personal"]      # 하위도 스캔 대상인 묶음 폴더(config.json bundle_dirs)
MTIME_EXCLUDE_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", SECRET}
MTIME_EXCLUDE_FILES = {".DS_Store"}
MTIME_DEPTH = 3
STALE_DAYS = 30

MACHINES_DIR = "machines"                 # 기본 맥이 아닌 맥의 위치표: machines/<맥>.md
MACHINE_ENV = "WORKSPACE_INDEX_MACHINE"   # 맥 이름 덮어쓰기(테스트용)
DEFAULT_MACHINE = "main"                  # 카드 path·covers·excluded.md 가 가리키는 맥(config.json default_machine)
NOT_HERE = "-"                            # 카드 path 값: 기본 맥에는 이 폴더가 없음
MACHINE_TABLES = {                        # 위치표의 표 머리글 → 종류
    ("카드", "경로"): "paths",
    ("폴더", "정본 카드"): "covers",
    ("폴더", "사유"): "excluded",
}

CONFIG_FILE = "config.json"               # 저장소 루트의 설정. 없으면 위 기본값

PROJECTS_HEADER = "<!-- 자동 생성 파일. scripts/catalog.py build 로 다시 만든다. 직접 수정 금지. -->"


def nfc(s):
    """NFC 정규화."""
    return unicodedata.normalize("NFC", s)


def load_config(repo_root):
    """config.json 으로 GROUPS·BUNDLE_DIRS·DEFAULT_MACHINE 을 바꾼다. 오류 문장 목록을 돌려준다."""
    global GROUPS, BUNDLE_DIRS, DEFAULT_MACHINE
    path = os.path.join(repo_root, CONFIG_FILE)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return ["JSON 으로 읽을 수 없음 (%s)" % exc]
    if not isinstance(data, dict):
        return ["최상위가 객체가 아님"]
    errors = []
    unknown = sorted(set(data) - {"groups", "bundle_dirs", "default_machine"})
    if unknown:
        errors.append("모르는 키: %s" % ", ".join(unknown))
    groups = data.get("groups", GROUPS)
    if (not isinstance(groups, list) or not groups
            or not all(isinstance(g, str) and g.strip() for g in groups)):
        errors.append("groups 는 비어 있지 않은 문자열 목록이어야 함")
    bundles = data.get("bundle_dirs", BUNDLE_DIRS)
    if not isinstance(bundles, list) or not all(
            isinstance(b, str) and b and "/" not in b and b != SECRET for b in bundles):
        errors.append("bundle_dirs 는 폴더 이름(/ 없음) 목록이어야 함")
    machine = data.get("default_machine", DEFAULT_MACHINE)
    if not isinstance(machine, str) or not NAME_RE.match(machine):
        errors.append("default_machine 은 영문 소문자·숫자·- 이름이어야 함")
    if not errors:
        GROUPS = [nfc(g) for g in groups]
        BUNDLE_DIRS = [nfc(b) for b in bundles]
        DEFAULT_MACHINE = machine
    return errors


# ---------------------------------------------------------------------------
# 경로 도우미 (NFC 비교, secret 차단)
# ---------------------------------------------------------------------------

class FS:
    """디렉터리 목록을 NFC 로 캐시해 두고 경로를 찾는다. 파일 내용은 읽지 않는다."""

    def __init__(self):
        self._cache = {}

    def listing(self, real_dir):
        """real_dir 의 항목을 {NFC 이름: 실제 이름} 으로 돌려준다. secret 은 뺀다."""
        if real_dir in self._cache:
            return self._cache[real_dir]
        table = {}
        try:
            for entry in os.listdir(real_dir):
                if nfc(entry) == SECRET:
                    continue
                table[nfc(entry)] = entry
        except OSError:
            pass
        self._cache[real_dir] = table
        return table

    def resolve(self, base, rel):
        """base 아래 상대경로 rel(NFC) 의 실제 경로. 없으면 None."""
        cur = base
        for part in rel.split("/"):
            if part in ("", ".", ".."):
                return None
            if nfc(part) == SECRET:
                return None
            table = self.listing(cur)
            real = table.get(nfc(part))
            if real is None:
                return None
            cur = os.path.join(cur, real)
        return cur

    def is_dir(self, base, rel):
        real = self.resolve(base, rel)
        return real is not None and os.path.isdir(real)

    def is_file(self, base, rel):
        real = self.resolve(base, rel)
        return real is not None and os.path.isfile(real)

    def exists(self, base, rel):
        real = self.resolve(base, rel)
        return real is not None and os.path.lexists(real)

    def subdirs(self, real_dir):
        """숨김·secret 을 뺀 하위 디렉터리 이름(NFC) 목록."""
        out = []
        for name_nfc, real in self.listing(real_dir).items():
            if real.startswith("."):
                continue
            if os.path.isdir(os.path.join(real_dir, real)):
                out.append(name_nfc)
        return sorted(out)


def scan_folders(workspace_root, fs, repo_name):
    """스캔 범위: 루트 바로 아래 + BUNDLE_DIRS 바로 아래. 반환값은 NFC 상대경로 목록."""
    result = []
    for name in fs.subdirs(workspace_root):
        if name in SKIP_TOP or name == repo_name:
            continue
        result.append(name)
    for bundle in BUNDLE_DIRS:
        real = fs.resolve(workspace_root, bundle)
        if real is None or not os.path.isdir(real):
            continue
        for name in fs.subdirs(real):
            result.append(bundle + "/" + name)
    return result


# ---------------------------------------------------------------------------
# last_activity 계산 (docs/card-format.md "활동일")
# ---------------------------------------------------------------------------

def _git_last_date(real_dir):
    """`.git` 이 있으면 마지막 커밋 날짜(YYYY-MM-DD). 커밋이 없거나 실패하면 None."""
    if not os.path.lexists(os.path.join(real_dir, ".git")):
        return None
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    try:
        proc = subprocess.run(
            ["git", "-C", real_dir, "log", "-1", "--format=%cs"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.decode("utf-8", "replace").strip()
    return out if DATE_RE.match(out) else None


def _newest_mtime(real_dir, depth):
    """깊이 depth 까지의 파일 중 가장 최근 mtime. 제외 규칙은 MTIME_EXCLUDE_*. 파일 내용은 읽지 않는다."""
    newest = None
    try:
        entries = list(os.scandir(real_dir))
    except OSError:
        return None
    for entry in entries:
        name = nfc(entry.name)
        if name == SECRET:
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                if depth <= 1 or name.startswith(".") or name in MTIME_EXCLUDE_DIRS:
                    continue
                sub = _newest_mtime(entry.path, depth - 1)
                if sub is not None and (newest is None or sub > newest):
                    newest = sub
            elif entry.is_file(follow_symlinks=False):
                if name in MTIME_EXCLUDE_FILES:
                    continue
                mt = entry.stat(follow_symlinks=False).st_mtime
                if newest is None or mt > newest:
                    newest = mt
        except OSError:
            continue
    return newest


def compute_last_activity(real_dir):
    """폴더의 마지막 활동일. git 날짜 → 파일 mtime → 폴더 mtime 순."""
    date = _git_last_date(real_dir)
    if date:
        return date
    mt = _newest_mtime(real_dir, MTIME_DEPTH)
    if mt is None:
        try:
            mt = os.stat(real_dir).st_mtime
        except OSError:
            return None
    return datetime.date.fromtimestamp(mt).isoformat()


# ---------------------------------------------------------------------------
# 카드 파싱 (docs/card-format.md "문법")
# ---------------------------------------------------------------------------

class Card:
    def __init__(self, filename, text):
        self.filename = filename            # 예: budget-book.md
        self.stem = filename[:-3]
        self.text = text
        self.fields = {}                    # 파싱된 값 (스칼라 str / 리스트 list)
        self.errors = []                    # 카드 자체 문법·필드 오류
        self.body = ""
        self.fm_end_line = None             # 닫는 --- 의 줄 번호

    @property
    def name(self):
        """보고·링크용 식별자. 유효한 카드에서는 name 필드와 같다(파일명 stem)."""
        return self.stem

    @property
    def field_name(self):
        """name 필드값(related 해석용). 없으면 stem."""
        v = self.fields.get("name")
        return v if isinstance(v, str) else self.stem

    def get_list(self, key):
        v = self.fields.get(key)
        return v if isinstance(v, list) else []

    def get_str(self, key):
        v = self.fields.get(key)
        return v if isinstance(v, str) else ""

    @property
    def removed(self):
        return self.get_str("status") == "removed"


def _parse_list(raw):
    """인라인 리스트 `[a, b]` → 항목 목록. 문법 위반이면 오류 문자열을 돌려준다."""
    inner = raw[1:-1].strip()
    if inner == "":
        return [], None
    items = []
    for piece in inner.split(","):
        item = piece.strip()
        if item == "":
            return None, "빈 항목(쉼표 뒤에 값이 없음)"
        if "[" in item or "]" in item:
            return None, "항목 안에 대괄호 금지: " + item
        items.append(nfc(item))
    return items, None


def parse_card(filename, text):
    """카드 텍스트를 Card 로. 문법 위반은 card.errors 에 쌓는다."""
    card = Card(filename, text)
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        card.errors.append("머리부가 `---` 로 시작하지 않음")
        return card
    end = None
    seen = set()
    for i in range(1, len(lines)):
        line = lines[i]
        if line == "---":
            end = i
            break
        if line.strip() == "" or line.startswith("#"):
            continue
        if line[0] in " \t":
            card.errors.append("%d행: 들여쓰기·중첩 값 금지" % (i + 1))
            continue
        m = KEY_RE.match(line)
        if not m:
            card.errors.append("%d행: `key: value` 형식이 아님" % (i + 1))
            continue
        key, raw = m.group(1), m.group(2).strip()
        if key not in FIELDS:
            card.errors.append("알 수 없는 필드 `%s`" % key)
            continue
        if key in seen:
            card.errors.append("필드 `%s` 중복" % key)
            continue
        seen.add(key)
        if raw.startswith("["):
            if not raw.endswith("]"):
                card.errors.append("필드 `%s`: 리스트가 `]` 로 끝나지 않음(값 뒤 주석·여러 줄 값 금지)" % key)
                continue
            items, err = _parse_list(raw)
            if err:
                card.errors.append("필드 `%s`: %s" % (key, err))
                continue
            card.fields[key] = items
        else:
            card.fields[key] = nfc(raw)
    if end is None:
        card.errors.append("머리부를 닫는 `---` 가 없음")
        return card
    card.fm_end_line = end
    card.body = "\n".join(lines[end + 1:])
    for key in FIELDS:
        if key not in card.fields:
            card.errors.append("필수 필드 `%s` 누락" % key)
    for key in FIELDS:
        if key not in card.fields:
            continue
        val = card.fields[key]
        if key in LIST_FIELDS and not isinstance(val, list):
            card.errors.append("필드 `%s` 는 리스트 `[...]` 여야 함" % key)
            del card.fields[key]
        elif key in SCALAR_FIELDS and not isinstance(val, str):
            card.errors.append("필드 `%s` 는 스칼라여야 함" % key)
            del card.fields[key]
        elif key in SCALAR_FIELDS and val == "":
            card.errors.append("필드 `%s` 가 비어 있음" % key)
            del card.fields[key]
    return card


def _valid_date(s):
    if not DATE_RE.match(s):
        return False
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        return False
    return True


def _check_rel_folder(value):
    """path·covers 형식 검사(깊이 1, 또는 work/·personal/ 아래 깊이 2). 오류 문자열 또는 None."""
    if value.startswith("./") or value.startswith("/"):
        return "`./` 나 `/` 로 시작할 수 없음"
    if value.endswith("/"):
        return "끝에 `/` 금지"
    parts = value.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        return "빈 요소·`.`·숨김 요소 금지"
    if any(p == SECRET for p in parts):
        return "`secret` 경로 금지"
    if len(parts) == 1:
        return None
    if len(parts) == 2 and parts[0] in BUNDLE_DIRS:
        return None
    return "깊이 1 이거나 work/·personal/ 아래 깊이 2 여야 함"


def _check_rel_sub(value):
    """entry_docs·read_scope 형식 검사(path 기준 상대경로)."""
    if value.startswith("/") or value.startswith("./"):
        return "`./` 나 `/` 로 시작할 수 없음"
    parts = value.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "빈 요소·`.`·`..` 금지"
    if any(p == SECRET for p in parts):
        return "`secret` 경로 금지"
    return None


def validate_card_shape(card):
    """파일시스템을 보지 않는 카드 단독 검사(문법·필드·본문 제목)."""
    errs = card.errors
    f = card.fields
    if "name" in f:
        if not NAME_RE.match(f["name"]):
            errs.append("name 패턴 위반: `%s`" % f["name"])
        if f["name"] != card.stem:
            errs.append("name `%s` 이 파일명 `%s` 과 다름" % (f["name"], card.filename))
    if "status" in f and f["status"] not in STATUSES:
        errs.append("status 값 `%s` 은 허용되지 않음" % f["status"])
    if "group" in f and f["group"] not in GROUPS:
        errs.append("group 값 `%s` 은 허용되지 않음" % f["group"])
    if "sensitive" in f and f["sensitive"] not in SENSITIVES:
        errs.append("sensitive 값 `%s` 은 허용되지 않음" % f["sensitive"])
    for key in DATE_FIELDS:
        if key in f and not _valid_date(f[key]):
            errs.append("%s 가 YYYY-MM-DD 날짜가 아님: `%s`" % (key, f[key]))
    if f.get("path") == NOT_HERE:
        if card.get_list("covers"):
            errs.append("path 가 `-`(기본 맥에 없음)인데 covers 가 있음")
    elif "path" in f:
        err = _check_rel_folder(f["path"])
        if err:
            errs.append("path 형식 위반: " + err)
    for item in card.get_list("covers"):
        err = _check_rel_folder(item)
        if err:
            errs.append("covers `%s` 형식 위반: %s" % (item, err))
    for key in ("entry_docs", "read_scope"):
        for item in card.get_list(key):
            err = _check_rel_sub(item)
            if err:
                errs.append("%s `%s` 형식 위반: %s" % (key, item, err))
    if "entry_docs" in f and not f["entry_docs"]:
        errs.append("entry_docs 는 비울 수 없음")
    if f.get("sensitive") == "pii" and "read_scope" in f and not f["read_scope"]:
        errs.append("sensitive: pii 인데 read_scope 가 비어 있음")
    if card.fm_end_line is not None:
        headings = [l.rstrip() for l in card.body.split("\n") if l.startswith("## ")]
        if headings != HEADINGS:
            errs.append("본문 절 제목이 %s 순서여야 함(현재: %s)"
                        % (", ".join(HEADINGS), ", ".join(headings) or "없음"))
    return errs


# ---------------------------------------------------------------------------
# 저장소 읽기
# ---------------------------------------------------------------------------

def load_cards(repo_root):
    """catalog/*.md 를 파일명 순으로 읽는다. 카드 목록과 저장소 수준 오류를 돌려준다."""
    cards, errors = [], []
    cat_dir = os.path.join(repo_root, "catalog")
    if not os.path.isdir(cat_dir):
        return cards, errors
    for fn in sorted(os.listdir(cat_dir)):
        if not fn.endswith(".md") or fn.startswith("."):
            continue
        full = os.path.join(cat_dir, fn)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, "r", encoding="utf-8", newline="") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as e:
            errors.append(("ERROR", fn[:-3], "카드를 읽을 수 없음: %s" % e))
            continue
        cards.append(parse_card(nfc(fn), text))
    return cards, errors


def load_excluded(repo_root):
    """excluded.md 의 `| 폴더 | 사유 |` 표를 [(폴더, 사유)] 로. 오류 목록도 함께."""
    rows, errors = [], []
    path = os.path.join(repo_root, "excluded.md")
    if not os.path.isfile(path):
        errors.append("excluded.md 가 없음")
        return rows, errors
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    table = [l.strip() for l in lines if l.strip().startswith("|")]
    if len(table) < 2:
        errors.append("excluded.md 에 `| 폴더 | 사유 |` 표가 없음")
        return rows, errors
    header = [c.strip() for c in table[0].strip("|").split("|")]
    if header[:2] != ["폴더", "사유"]:
        errors.append("excluded.md 표 머리글이 `| 폴더 | 사유 |` 가 아님")
        return rows, errors
    seen = set()
    for line in table[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] == "":
            errors.append("excluded.md 행 형식 오류: " + line)
            continue
        folder = nfc(cells[0])
        if folder == SECRET:
            errors.append("excluded.md 에 `secret` 을 적지 않는다")
            continue
        if folder in seen:
            errors.append("excluded.md 폴더 중복: " + folder)
            continue
        seen.add(folder)
        rows.append((folder, nfc(cells[1])))
    return rows, errors


# ---------------------------------------------------------------------------
# 맥별 위치
# ---------------------------------------------------------------------------

class Machine:
    """machines/<맥>.md 위치표 한 장. 표 세 개(카드 위치, 정본이 아닌 폴더, 카드 없는 폴더)."""

    def __init__(self, name):
        self.name = name
        self.paths = []                     # [(카드 name, 경로)]
        self.covers = []                    # [(폴더, 카드 name)]
        self.excluded = []                  # [(폴더, 사유)]
        self.errors = []

    @property
    def who(self):
        return "%s/%s.md" % (MACHINES_DIR, self.name)


def _table_cell(raw):
    """위치표 칸 값. 앞뒤 공백은 버리되, `…` 로 감싼 값은 안쪽을 그대로 쓴다(끝에 공백이 붙은 폴더 이름용)."""
    cell = raw.strip()
    if len(cell) >= 2 and cell[0] == "`" and cell[-1] == "`":
        cell = cell[1:-1]
    return nfc(cell)


def parse_machine(name, text):
    """위치표 텍스트를 Machine 으로. 연속한 `|` 줄 묶음 하나가 표 하나다."""
    m = Machine(name)
    tables, cur = [], None
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("|"):
            if cur is None:
                cur = []
                tables.append(cur)
            cur.append([_table_cell(c) for c in line.strip("|").split("|")])
        else:
            cur = None
    seen_kinds = set()
    for table in tables:
        kind = MACHINE_TABLES.get(tuple(table[0][:2]))
        if kind is None:
            m.errors.append("알 수 없는 표 머리글: | %s |" % " | ".join(table[0]))
            continue
        if kind in seen_kinds:
            m.errors.append("`| %s |` 표가 둘 이상" % " | ".join(table[0][:2]))
            continue
        seen_kinds.add(kind)
        for cells in table[2:]:
            if len(cells) < 2 or cells[0] == "" or cells[1] == "":
                m.errors.append("행 형식 오류: | %s |" % " | ".join(cells))
                continue
            getattr(m, kind).append((cells[0], cells[1]))
    if "paths" not in seen_kinds:
        m.errors.append("`| 카드 | 경로 |` 표가 없음")
    folders = {}
    for card, path in m.paths:
        folders.setdefault(path, []).append("카드 %s" % card)
    for folder, card in m.covers:
        folders.setdefault(folder, []).append("covers(%s)" % card)
    for folder, _ in m.excluded:
        folders.setdefault(folder, []).append("카드 없는 폴더")
    for folder, roles in folders.items():
        err = _check_rel_folder(folder)
        if err:
            m.errors.append("`%s` 형식 위반: %s" % (folder, err))
        if len(roles) > 1:
            m.errors.append("`%s` 가 둘 이상에 적힘: %s" % (folder, " / ".join(roles)))
    cards = [c for c, _ in m.paths]
    for card in sorted(set(c for c in cards if cards.count(c) > 1)):
        m.errors.append("카드 `%s` 의 경로가 둘 이상" % card)
    return m


def load_machines(repo_root):
    """machines/*.md 를 이름 순으로 읽는다. {맥 이름: Machine}"""
    result = {}
    mdir = os.path.join(repo_root, MACHINES_DIR)
    if not os.path.isdir(mdir):
        return result
    for fn in sorted(os.listdir(mdir)):
        if not fn.endswith(".md") or fn.startswith("."):
            continue
        name = nfc(fn[:-3])
        try:
            with open(os.path.join(mdir, fn), "r", encoding="utf-8") as fh:
                m = parse_machine(name, fh.read())
        except (OSError, UnicodeDecodeError) as e:
            m = Machine(name)
            m.errors.append("위치표를 읽을 수 없음: %s" % e)
        if not NAME_RE.match(name):
            m.errors.append("맥 이름 패턴 위반: `%s`" % name)
        if name == DEFAULT_MACHINE:
            m.errors.append("기본 맥(%s)의 위치는 카드 path·covers·excluded.md 에 둔다(위치표 금지)"
                            % DEFAULT_MACHINE)
        result[name] = m
    return result


def machine_errors(cards, machines):
    """위치표와 카드 사이의 구조 오류. [(누구, 설명)]"""
    errs = []
    names = {c.field_name for c in cards}
    for m in machines.values():
        for msg in m.errors:
            errs.append((m.who, msg))
        for card, _ in m.paths:
            if card not in names:
                errs.append((m.who, "카드 `%s` 가 catalog 에 없음" % card))
        for folder, card in m.covers:
            if card not in names:
                errs.append((m.who, "`%s` 의 정본 카드 `%s` 가 catalog 에 없음" % (folder, card)))
    located = {card for m in machines.values() for card, _ in m.paths}
    for c in cards:
        if c.get_str("path") == NOT_HERE and c.field_name not in located:
            errs.append((c.name, "path 가 `-` 인데 어느 맥 위치표에도 경로가 없음"))
    return errs


class Layout:
    """한 맥에서 본 카드 위치. 기본 맥은 카드 path·covers·excluded.md, 다른 맥은 위치표."""

    def __init__(self, name, default, paths, covers, excluded):
        self.name = name
        self.default = default
        self.paths = paths                  # {카드 name: 상대경로}
        self.covers = covers                # [(폴더, 카드 name)]
        self.excluded = excluded            # [(폴더, 사유)]


def machine_name(workspace_root):
    """지금 맥 이름. 작업공간 루트가 /Users/<맥>/ai 이므로 그 부모 폴더 이름."""
    env = os.environ.get(MACHINE_ENV)
    if env:
        return nfc(env)
    return nfc(os.path.basename(os.path.dirname(os.path.normpath(workspace_root))))


def current_layout(cards, excluded, machines, workspace_root):
    name = machine_name(workspace_root)
    m = machines.get(name)
    if m is not None and name != DEFAULT_MACHINE:
        return Layout(name, False, dict(m.paths), list(m.covers), list(m.excluded))
    paths = {c.name: c.fields["path"] for c in cards
             if "path" in c.fields and c.fields["path"] != NOT_HERE}
    covers = [(item, c.name) for c in cards for item in c.get_list("covers")]
    return Layout(DEFAULT_MACHINE, True, paths, covers, list(excluded))


def card_machines(card, machines):
    """카드 폴더가 있는 맥 이름 목록(기본 맥 먼저)."""
    out = []
    if card.get_str("path") and card.get_str("path") != NOT_HERE:
        out.append(DEFAULT_MACHINE)
    for name in sorted(machines):
        if any(c == card.field_name for c, _ in machines[name].paths):
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# 점검
# ---------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.lines = []
        self.errors = 0
        self.warns = 0

    def error(self, who, msg):
        self.errors += 1
        self.lines.append("ERROR %s: %s" % (who, msg))

    def warn(self, who, msg):
        self.warns += 1
        self.lines.append("WARN %s: %s" % (who, msg))


def compute_activities(cards, layout, workspace_root, fs):
    """removed 가 아닌 카드 중 이 맥에 폴더가 있는 것의 활동일을 계산한다. {name: 날짜 또는 None}"""
    result = {}
    for card in cards:
        path = layout.paths.get(card.name)
        if card.removed or path is None:
            continue
        real = fs.resolve(workspace_root, path)
        if real is None or not os.path.isdir(real):
            continue
        result[card.name] = compute_last_activity(real)
    return result


def run_check(repo_root, workspace_root, coverage=True, today=None, out=sys.stdout):
    """check 본체. 종료코드(0/1)를 돌려준다."""
    today = today or datetime.date.today()
    fs = FS()
    rep = Report()
    repo_name = nfc(os.path.basename(os.path.normpath(repo_root)))

    cards, load_errors = load_cards(repo_root)
    for _, who, msg in load_errors:
        rep.error(who, msg)
    excluded, exc_errors = load_excluded(repo_root)
    for msg in exc_errors:
        rep.error("excluded.md", msg)

    machines = load_machines(repo_root)
    layout = current_layout(cards, excluded, machines, workspace_root)

    # 1) 카드 단독 검사 + 위치표 구조
    for card in cards:
        for msg in validate_card_shape(card):
            rep.error(card.name, msg)
    for who, msg in machine_errors(cards, machines):
        rep.error(who, msg)

    # 2) 카드 간 검사: name·path 중복(이 맥 기준), related, aliases
    by_name = {}
    for card in cards:
        by_name.setdefault(card.field_name, []).append(card)
    for name, group in by_name.items():
        if len(group) > 1:
            for c in group:
                rep.error(c.name, "name `%s` 중복(%d장)" % (name, len(group)))
    by_path = {}
    for name, path in sorted(layout.paths.items()):
        by_path.setdefault(path, []).append(name)
    for path, names in by_path.items():
        if len(names) > 1:
            for n in names:
                rep.error(n, "path `%s` 를 다른 카드와 공유: %s" % (path, ", ".join(names)))
    for card in cards:
        for rel in card.get_list("related"):
            if rel not in by_name:
                rep.error(card.name, "related `%s` 가 카드로 풀리지 않음" % rel)
    alias_owner = {}
    for card in cards:
        for alias in card.get_list("aliases"):
            alias_owner.setdefault(alias, []).append(card.name)
    for alias, names in sorted(alias_owner.items()):
        if len(names) > 1:
            for n in names:
                others = [m for m in names if m != n]
                rep.warn(n, "alias `%s` 가 다른 카드와 겹침: %s" % (alias, ", ".join(others)))

    # 3) 파일시스템 검사(이 맥 기준): path·entry_docs·covers·read_scope 존재
    #    다른 맥의 사본은 낡았을 수 있어 entry_docs·read_scope 부재는 경고로만 본다.
    covers_of = {}
    for folder, name in layout.covers:
        covers_of.setdefault(name, []).append(folder)
    for card in cards:
        path = layout.paths.get(card.name)
        for item in covers_of.get(card.name, []):
            if not fs.is_dir(workspace_root, item):
                rep.error(card.name, "covers `%s` 가 존재하지 않음" % item)
        if path is None:
            continue
        path_real = fs.resolve(workspace_root, path)
        path_exists = path_real is not None and os.path.isdir(path_real)
        if card.removed:
            if path_exists:
                rep.warn(card.name, "removed 카드인데 path `%s` 가 다시 존재함" % path)
        elif not path_exists:
            rep.error(card.name, "path `%s` 가 존재하지 않음" % path)
        if card.removed or not path_exists:
            continue
        missing = rep.error if layout.default else rep.warn
        for item in card.get_list("entry_docs"):
            if not fs.is_file(path_real, item):
                missing(card.name, "entry_docs `%s` 가 파일로 존재하지 않음" % item)
        for item in card.get_list("read_scope"):
            if not fs.exists(path_real, item):
                missing(card.name, "read_scope `%s` 가 존재하지 않음" % item)

    # 4) 날짜 경고. last_activity 는 어느 맥에서 본 것이든 가장 늦은 날짜를 남긴다.
    activities = compute_activities(cards, layout, workspace_root, fs)
    for card in cards:
        f = card.fields
        la, lv = f.get("last_activity"), f.get("last_verified")
        if card.name in activities and activities[card.name] and la and _valid_date(la):
            if activities[card.name] > la:
                rep.warn(card.name, "last_activity 저장값 %s < 이 맥 계산값 %s (build 필요)"
                         % (la, activities[card.name]))
        if la and lv and _valid_date(la) and _valid_date(lv) and la > lv:
            rep.warn(card.name, "last_activity %s 가 last_verified %s 보다 뒤(확인 뒤 폴더가 바뀜)" % (la, lv))
        if lv and _valid_date(lv):
            age = (today - datetime.date.fromisoformat(lv)).days
            if age > STALE_DAYS:
                rep.warn(card.name, "last_verified %s 가 30일보다 오래됨(%d일)" % (lv, age))

    # 5) 카드 없는 폴더가 카드 path·covers 와 겹침(이 맥 기준)
    covered_by = {}
    for folder, name in layout.covers:
        covered_by.setdefault(folder, []).append(name)
    exc_who = "excluded.md" if layout.default else "%s/%s.md" % (MACHINES_DIR, layout.name)
    for folder, _ in layout.excluded:
        if folder in by_path:
            rep.error(exc_who, "`%s` 가 카드 %s 의 path 와 겹침" % (folder, ", ".join(by_path[folder])))
        if folder in covered_by:
            rep.error(exc_who, "`%s` 가 카드 %s 의 covers 와 겹침" % (folder, ", ".join(covered_by[folder])))

    # 6) 6.1(c) 스캔 범위 커버리지(이 맥 기준) + PROJECTS.md 일치
    if coverage:
        excluded_set = {folder for folder, _ in layout.excluded}
        for folder in scan_folders(workspace_root, fs, repo_name):
            roles = []
            if folder in by_path:
                roles.append("카드 path(%s)" % ", ".join(by_path[folder]))
            if folder in covered_by:
                roles.append("covers(%s)" % ", ".join(covered_by[folder]))
            if folder in excluded_set:
                roles.append(exc_who)
            if not roles:
                rep.error(folder, "미등록 폴더")
            elif len(roles) > 1:
                rep.error(folder, "둘 이상에 속함: " + " / ".join(roles))
        stored_path = os.path.join(repo_root, "PROJECTS.md")
        if not os.path.isfile(stored_path):
            rep.error("PROJECTS.md", "파일이 없음(build 필요)")
        else:
            with open(stored_path, "r", encoding="utf-8", newline="") as fh:
                stored = fh.read()
            generated = render_projects(cards, excluded, machines, today)
            if _strip_gen_line(stored) != _strip_gen_line(generated):
                rep.error("PROJECTS.md", "저장된 내용이 생성 결과와 다름(build 필요)")

    for line in rep.lines:
        out.write(line + "\n")
    out.write("카드 %d장, 오류 %d, 경고 %d\n" % (len(cards), rep.errors, rep.warns))
    return 1 if rep.errors else 0


def _strip_gen_line(text):
    return "\n".join(l for l in text.split("\n") if not l.startswith("생성 "))


# ---------------------------------------------------------------------------
# PROJECTS.md 생성
# ---------------------------------------------------------------------------

def _cell(s):
    return nfc(s).replace("|", "\\|").replace("\n", " ")


def _link(name):
    return "[%s](catalog/%s.md)" % (name, name)


def render_projects(cards, excluded, machines, today):
    """카드·excluded.md·위치표로 PROJECTS.md 본문을 만든다. 생성일 외에는 결정적(어느 맥에서 만들어도 같다)."""
    live = [c for c in cards if not c.removed]
    removed = [c for c in cards if c.removed]
    out = [PROJECTS_HEADER, "# 프로젝트 지도", "",
           "생성 %s · 카드 %d장(removed 제외) · 활동 기준 last_activity" % (today.isoformat(), len(live)),
           ""]
    for group in GROUPS:
        members = [c for c in live if c.get_str("group") == group]
        members.sort(key=lambda c: (STATUS_ORDER.get(c.get_str("status"), 99), c.name))
        out.append("## " + group)
        out.append("")
        if not members:
            out.append("(없음)")
        else:
            out.append("| 카드 | 요약 | 상태 | 최근 활동 | 들어갈 때 | 별칭 | 맥 |")
            out.append("|---|---|---|---|---|---|---|")
            for c in members:
                out.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                    _link(c.name), _cell(c.get_str("summary")), _cell(c.get_str("status")),
                    _cell(c.get_str("last_activity")),
                    _cell(" → ".join(c.get_list("entry_docs"))),
                    _cell(", ".join(c.get_list("aliases"))),
                    _cell(", ".join(card_machines(c, machines)))))
        out.append("")

    out.append("## 정본이 아닌 폴더")
    out.append("")
    rows = []
    for c in cards:
        for folder in c.get_list("covers"):
            rows.append((folder, c.name))
    rows.sort()
    if not rows:
        out.append("(없음)")
    else:
        out.append("| 폴더 | 정본 카드 | 비고 |")
        out.append("|---|---|---|")
        for folder, name in rows:
            out.append("| %s | %s | 카드 '관련 폴더' 절 참고 |" % (_cell(folder), _link(name)))
    out.append("")

    out.append("## 카드 없는 폴더")
    out.append("")
    if not excluded:
        out.append("(없음)")
    else:
        out.append("| 폴더 | 사유 |")
        out.append("|---|---|")
        for folder, reason in excluded:
            out.append("| %s | %s |" % (_cell(folder), _cell(reason)))
    out.append("")

    out.append("## 폴더가 사라진 카드")
    out.append("")
    if not removed:
        out.append("(없음)")
    else:
        out.append("| 카드 | 옛 경로 | 최근 활동 |")
        out.append("|---|---|---|")
        for c in sorted(removed, key=lambda c: c.name):
            out.append("| %s | %s | %s |" % (
                _link(c.name), _cell(c.get_str("path")), _cell(c.get_str("last_activity"))))
    out.append("")

    out.append("## 맥별 위치")
    out.append("")
    out.append("위 세 표와 카드 `path`는 기본 맥(%s) 기준이다. 다른 맥에서는 그 맥의 위치표를 본다."
               % DEFAULT_MACHINE)
    out.append("")
    if not machines:
        out.append("(없음)")
    else:
        out.append("| 맥 | 위치표 | 카드 | 정본이 아닌 폴더 | 카드 없는 폴더 |")
        out.append("|---|---|---|---|---|")
        for name in sorted(machines):
            m = machines[name]
            out.append("| %s | [%s](%s) | %d | %d | %d |" % (
                _cell(name), m.who, m.who, len(m.paths), len(m.covers), len(m.excluded)))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def _rewrite_last_activity(text, date):
    """머리부 안의 `last_activity:` 줄만 바꾼다. 나머지는 바이트 그대로."""
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i] == "---":
            break
        if lines[i].startswith("last_activity:"):
            lines[i] = "last_activity: " + date
            break
    return "\n".join(lines)


def run_build(repo_root, workspace_root, today=None, out=sys.stdout):
    """build 본체. 카드 구조 오류가 있으면 아무것도 쓰지 않고 1 을 돌려준다."""
    today = today or datetime.date.today()
    fs = FS()
    cards, load_errors = load_cards(repo_root)
    excluded, exc_errors = load_excluded(repo_root)
    machines = load_machines(repo_root)
    fatal = [(who, msg) for _, who, msg in load_errors] + [("excluded.md", m) for m in exc_errors]
    for card in cards:
        for msg in validate_card_shape(card):
            fatal.append((card.name, msg))
    fatal += machine_errors(cards, machines)
    if fatal:
        for who, msg in fatal:
            out.write("ERROR %s: %s\n" % (who, msg))
        out.write("카드 구조 오류 %d건. build 를 중단한다(아무것도 쓰지 않음).\n" % len(fatal))
        return 1

    layout = current_layout(cards, excluded, machines, workspace_root)
    activities = compute_activities(cards, layout, workspace_root, fs)
    updated = 0
    for card in cards:
        date = activities.get(card.name)
        if not date or date <= card.fields.get("last_activity", ""):
            continue
        new_text = _rewrite_last_activity(card.text, date)
        if new_text == card.text:
            continue
        card.fields["last_activity"] = date
        card.text = new_text
        with open(os.path.join(repo_root, "catalog", card.filename), "w",
                  encoding="utf-8", newline="") as fh:
            fh.write(new_text)
        updated += 1

    content = render_projects(cards, excluded, machines, today)
    with open(os.path.join(repo_root, "PROJECTS.md"), "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    live = sum(1 for c in cards if not c.removed)
    out.write("카드 %d장(removed 제외 %d장), last_activity 갱신 %d장, PROJECTS.md 생성 (맥 %s)\n"
              % (len(cards), live, updated, layout.name))
    return 0


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

def locate_roots():
    """저장소 루트 = scripts/.. (이 파일 위치 기준), 작업공간 루트 = 그 부모."""
    here = os.path.dirname(os.path.realpath(__file__))
    repo_root = os.path.dirname(here)
    return repo_root, os.path.dirname(repo_root)


def main(argv=None):
    parser = argparse.ArgumentParser(description="작업공간 카탈로그 점검·빌드")
    sub = parser.add_subparsers(dest="command")
    p_check = sub.add_parser("check", help="카드·폴더·PROJECTS.md 를 검증한다(읽기 전용)")
    p_check.add_argument("--no-coverage", action="store_true",
                         help="미등록 폴더 검사와 PROJECTS.md 일치 검사를 건너뛴다")
    sub.add_parser("build", help="last_activity 를 갱신하고 PROJECTS.md 를 다시 만든다")
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    repo_root, workspace_root = locate_roots()
    config_errors = load_config(repo_root)
    if config_errors:
        for msg in config_errors:
            sys.stdout.write("ERROR %s: %s\n" % (CONFIG_FILE, msg))
        sys.stdout.write("카드 0장, 오류 %d, 경고 0\n" % len(config_errors))
        return 1
    if args.command == "check":
        return run_check(repo_root, workspace_root, coverage=not args.no_coverage)
    return run_build(repo_root, workspace_root)


if __name__ == "__main__":
    sys.exit(main())
