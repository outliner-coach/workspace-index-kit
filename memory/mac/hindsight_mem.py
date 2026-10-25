#!/usr/bin/env python3
"""hindsight-mem: 맥에서 Hindsight 기억 저장소를 쓰는 명령.

Python 3.9 표준 라이브러리만 쓴다. 비밀값(API 키)은 어디에도 출력하지 않는다.
부명령: resolve-bank, recent, recall, retain, forget, flush-queue, session-start-context.
"""
import argparse
import datetime
import hmac
import json
import os
import re
import secrets
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# API 경로 — 실제 서버에서 다르면 여기만 고친다. {bank}, {doc} 자리표시자.
API = {
    "health": "/health",
    "retain": "/v1/default/banks/{bank}/memories",
    "recall": "/v1/default/banks/{bank}/memories/recall",
    "recall_fallback": "/v1/default/banks/{bank}/recall",
    "documents": "/v1/default/banks/{bank}/documents",
    "document": "/v1/default/banks/{bank}/documents/{doc}",
    "memories_list": "/v1/default/banks/{bank}/memories/list",
}
DEFAULT_URL = ""   # 서버 주소는 ~/.config/hindsight/env 의 HINDSIGHT_URL 로 정한다
RESERVED_BANKS = ("workspace-index", "restore-test")
KINDS = ("handoff", "note", "correction", "retraction")
KIND_CONTEXT = {
    "handoff": "Claude Code 세션 인계문",
    "note": "Claude Code 세션 중간 기록",
    "correction": "Claude Code 기억 정정",
    "retraction": "Claude Code 기억 철회",
}
TEXT_KEYS = ("text", "content", "original_text", "raw_text")
TIME_KEYS = ("updated_at", "created_at", "timestamp", "mentioned_at", "occurred_start", "date")
RECENT_CAP = 10000
CMD_PATH = "~/.claude/scripts/hindsight-mem"


class BankError(Exception):
    """허용 목록 밖 은행 이름."""


# ---------------------------------------------------------------------------
# 경로·카드


def nfc(s):
    return unicodedata.normalize("NFC", s)


def norm_path(p):
    return nfc(os.path.normpath(os.path.abspath(os.path.expanduser(p))))


def repo_root():
    env = os.environ.get("HINDSIGHT_REPO_ROOT")
    if env:
        return norm_path(env)
    # memory/mac/hindsight_mem.py → 저장소 루트는 두 단계 위
    here = os.path.dirname(os.path.realpath(__file__))
    return norm_path(os.path.join(here, "..", ".."))


def workspace_root():
    env = os.environ.get("HINDSIGHT_WORKSPACE_ROOT")
    if env:
        return norm_path(env)
    return os.path.dirname(repo_root())


def machine_name():
    """지금 맥 이름. 작업공간 루트가 /Users/<맥>/ai 이므로 그 부모 폴더 이름(scripts/catalog.py와 같은 규칙)."""
    env = os.environ.get("WORKSPACE_INDEX_MACHINE")
    if env:
        return nfc(env)
    return nfc(os.path.basename(os.path.dirname(workspace_root())))


def load_machine_table():
    """이 맥의 위치표(machines/<맥>.md)를 ({카드: 경로}, {카드: [폴더]})로. 없으면 None(기본 맥: 카드 path)."""
    path = os.path.join(repo_root(), "machines", machine_name() + ".md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    paths, covers, kind = {}, {}, None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            kind = None
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        cells = [nfc(c[1:-1] if len(c) >= 2 and c[0] == c[-1] == "`" else c) for c in cells]
        if kind is None:
            kind = {("카드", "경로"): "paths", ("폴더", "정본 카드"): "covers"}.get(tuple(cells[:2]), "skip")
            continue
        if len(cells) < 2 or not cells[0] or not cells[1] or set(cells[0]) <= set("-: "):
            continue
        if kind == "paths":
            paths[cells[0]] = cells[1]
        elif kind == "covers":
            covers.setdefault(cells[1], []).append(cells[0])
    return paths, covers


def parse_frontmatter(text):
    """제한 문법 머리부(한 줄 key: value, 인라인 리스트)를 dict로 읽는다."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            out[key.strip()] = [x.strip() for x in inner.split(",") if x.strip()] if inner else []
        else:
            out[key.strip()] = val
    return out


def load_cards():
    """카드 목록. path·covers는 이 맥 기준(위치표가 있으면 그것, 없으면 카드 값). 이 맥에 없으면 path는 ""."""
    cat = os.path.join(repo_root(), "catalog")
    table = load_machine_table()
    cards = []
    try:
        names = sorted(os.listdir(cat))
    except OSError:
        return cards
    for fn in names:
        if not fn.endswith(".md"):
            continue
        try:
            with open(os.path.join(cat, fn), encoding="utf-8") as f:
                fm = parse_frontmatter(f.read())
        except (OSError, UnicodeDecodeError):
            continue
        name = fm.get("name")
        if not isinstance(name, str) or not name:
            continue
        name = nfc(name)
        sensitive = fm.get("sensitive") if isinstance(fm.get("sensitive"), str) else "none"
        if table is not None:
            cards.append({"name": name, "path": table[0].get(name, ""),
                          "covers": table[1].get(name, []), "sensitive": sensitive})
            continue
        path = nfc(fm["path"]) if isinstance(fm.get("path"), str) else ""
        covers = fm.get("covers")
        cards.append({
            "name": name,
            "path": "" if path == "-" else path,
            "covers": [nfc(c) for c in covers] if isinstance(covers, list) else [],
            "sensitive": sensitive,
        })
    return cards


def pii_banks():
    """카드에 sensitive: pii 라고 적힌 은행들."""
    return set(c["name"] for c in load_cards() if c.get("sensitive") == "pii")


def allowed_banks():
    return sorted(set(c["name"] for c in load_cards()) | set(RESERVED_BANKS))


def is_under(path, base):
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def resolve_bank(cwd):
    """cwd → 은행 이름 또는 None (memory/README.md "은행과 폴더"). 덮어쓰기 환경변수는 보지 않는다."""
    cwd = norm_path(cwd)
    ws = workspace_root()
    if cwd == ws:
        return "workspace-index"
    for base in (os.path.join(ws, "workspace-index"), repo_root()):
        if is_under(cwd, norm_path(base)):
            return "workspace-index"
    if not is_under(cwd, ws):
        return None
    cards = load_cards()
    best, best_len = None, -1
    for c in cards:
        if not c["path"]:
            continue
        base = norm_path(os.path.join(ws, c["path"]))
        if is_under(cwd, base) and len(base) > best_len:
            best, best_len = c["name"], len(base)
    if best:
        return best
    for c in cards:
        for cov in c["covers"]:
            base = norm_path(os.path.join(ws, cov))
            if is_under(cwd, base) and len(base) > best_len:
                best, best_len = c["name"], len(base)
    return best


def check_bank(name):
    if name not in allowed_banks():
        raise BankError("허용되지 않은 은행 이름입니다: %s (카드 name 또는 workspace-index·restore-test만 가능)" % name)
    return name


def current_bank(cwd=None, explicit=None):
    """은행을 정한다: --bank > HINDSIGHT_BANK_OVERRIDE > cwd 해석. 허용 목록 밖이면 BankError."""
    name = explicit or os.environ.get("HINDSIGHT_BANK_OVERRIDE") or resolve_bank(cwd or os.getcwd())
    if name is None:
        return None
    return check_bank(name)


# ---------------------------------------------------------------------------
# 설정·HTTP


def _strip_quotes(v):
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def load_config():
    path = os.environ.get("HINDSIGHT_ENV_FILE") or os.path.expanduser("~/.config/hindsight/env")
    cfg = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, _, v = s.partition("=")
                cfg[k.strip()] = _strip_quotes(v.strip())
    except (OSError, UnicodeDecodeError):
        pass
    for k in ("HINDSIGHT_API_KEY", "HINDSIGHT_URL"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    cfg["HINDSIGHT_URL"] = (cfg.get("HINDSIGHT_URL") or DEFAULT_URL).rstrip("/")
    return cfg


def _timeout():
    try:
        return float(os.environ.get("HINDSIGHT_TIMEOUT", "5"))
    except ValueError:
        return 5.0


def _retain_timeout():
    """저장은 서버가 언어모델로 사실을 추출하느라 수 초~수십 초 걸린다. 조회보다 길게 둔다."""
    try:
        return float(os.environ.get("HINDSIGHT_RETAIN_TIMEOUT", "30"))
    except ValueError:
        return 30.0


def api_path(key, bank=None, doc=None):
    return API[key].format(
        bank=urllib.parse.quote(bank or "", safe=""),
        doc=urllib.parse.quote(doc or "", safe=""))


def http(method, path, body=None, query=None, timeout=None):
    """요청을 보내고 JSON(또는 None)을 돌려준다. 실패는 예외로 올린다(재시도 없음)."""
    cfg = load_config()
    key = cfg.get("HINDSIGHT_API_KEY")
    if not key:
        raise RuntimeError("API 키 설정 없음")
    if not cfg["HINDSIGHT_URL"]:
        raise RuntimeError("HINDSIGHT_URL 설정 없음")
    url = cfg["HINDSIGHT_URL"] + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_unredirected_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout or _timeout()) as resp:
        raw = resp.read()
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return None


def describe_error(e):
    """예외를 비밀 없이 짧게 설명한다."""
    if isinstance(e, urllib.error.HTTPError):
        return "HTTP %s" % e.code
    if isinstance(e, urllib.error.URLError):
        return "연결 실패"
    if isinstance(e, RuntimeError):
        return str(e)
    return type(e).__name__


# ---------------------------------------------------------------------------
# 대기열


def queue_dir():
    return os.environ.get("HINDSIGHT_QUEUE_DIR") or os.path.expanduser("~/.local/state/hindsight-queue")


def enqueue(bank, body, stamp=None):
    d = queue_dir()
    os.makedirs(d, exist_ok=True)
    stamp = stamp or datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(d, "%s-%s.json" % (stamp, bank))
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(d, "%s-%s-%d.json" % (stamp, bank, n))
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"bank": bank, "body": body}, f, ensure_ascii=False)
    os.replace(tmp, path)   # 쓰는 중인 파일을 다른 세션이 읽지 않도록 완성 뒤 한 번에 옮긴다
    return path


def queued_files():
    d = queue_dir()
    try:
        return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".json")]
    except OSError:
        return []


def is_transient(e):
    """대기열에 넣어도 되는 실패인가: 연결·타임아웃·인증(401/403)·429·5xx. 그 밖의 4xx는 영구 거부."""
    if isinstance(e, urllib.error.HTTPError):
        # 404는 주소 설정이 틀렸거나 서버 스키마가 바뀐 경우일 수 있어 일시 오류로 본다.
        return e.code in (401, 403, 404, 408, 429) or e.code >= 500
    return True


def send_retain(bank, body, timeout=None):
    resp = http("POST", api_path("retain", bank), body=body, timeout=timeout)
    if isinstance(resp, dict) and resp.get("success") is False:
        raise RuntimeError("서버가 저장 실패를 알림")
    return resp


def flush_queue(max_items=None, max_seconds=None):
    """오래된 것부터 재전송. (보낸 건수, 남은 건수)를 돌려준다. 실패하면 거기서 멈춘다."""
    files = queued_files()
    start = time.monotonic()
    sent = 0
    for path in files:
        if max_items is not None and sent >= max_items:
            break
        timeout = None
        if max_seconds is not None:
            left = max_seconds - (time.monotonic() - start)
            if left <= 0:
                break
            timeout = min(_retain_timeout(), left)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            bank, body = data["bank"], data["body"]
        except (ValueError, KeyError, TypeError, OSError):
            _park_failed(path)       # 읽을 수 없는 파일: 옆으로 치우고 다음으로
            continue
        try:
            send_retain(bank, body, timeout=timeout or _retain_timeout())
        except Exception as e:
            if is_transient(e):
                break                # 서버 불통 등: 여기서 멈추고 다음 기회에
            _park_failed(path)       # 서버가 영구 거부(4xx): 옆으로 치우고
            break                    # 연쇄로 치워지지 않게 이번에는 여기서 멈춘다
        try:
            os.remove(path)
        except OSError:
            break
        sent += 1
    return sent, len(queued_files())


def _park_failed(path):
    """재전송이 영구히 실패한 파일을 queue/failed/ 로 옮긴다(지우지 않는다)."""
    try:
        d = os.path.join(queue_dir(), "failed")
        os.makedirs(d, exist_ok=True)
        os.replace(path, os.path.join(d, os.path.basename(path)))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 세션 토큰


def state_dir():
    return os.environ.get("HINDSIGHT_STATE_DIR") or os.path.expanduser("~/.local/state/hindsight")


def token_path(session_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)
    return os.path.join(state_dir(), "session-%s.token" % safe)


def ensure_token(session_id):
    path = token_path(session_id)
    try:
        with open(path, encoding="utf-8") as f:
            tok = f.read().strip()
        if tok:
            return tok
    except OSError:
        pass
    os.makedirs(state_dir(), exist_ok=True)
    tok = secrets.token_urlsafe(16)
    while tok.startswith("-"):  # '-'로 시작하면 --token 값이 옵션으로 읽힌다
        tok = secrets.token_urlsafe(16)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(tok)
    os.chmod(path, 0o600)
    return tok


def token_ok(token, session_id):
    if not token or not session_id:
        return False
    try:
        with open(token_path(session_id), encoding="utf-8") as f:
            saved = f.read().strip()
    except OSError:
        return False
    return bool(saved) and hmac.compare_digest(saved.encode("utf-8"), token.encode("utf-8"))


# ---------------------------------------------------------------------------
# 응답 해석(스키마 미확인 — 관대하게)


def _first(d, keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return None


def _items(resp, keys=("items", "results", "memories", "documents", "data")):
    if isinstance(resp, list):
        return [x for x in resp if isinstance(x, dict)]
    if isinstance(resp, dict):
        for k in keys:
            v = resp.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def _session_of(doc, doc_id):
    tags = doc.get("tags") if isinstance(doc, dict) else None
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, str) and t.startswith("session:"):
                return t[len("session:"):][:8]
    meta = doc.get("metadata") if isinstance(doc, dict) else None
    if isinstance(meta, dict) and isinstance(meta.get("session_id"), str):
        return meta["session_id"][:8]
    m = re.search(r"-([A-Za-z0-9]{1,8})$", doc_id or "")
    return m.group(1) if m else "?"


def fetch_recent(bank, limit=5, deadline=None):
    """최신 문서 목록 → 렌더된 텍스트. 서버 실패는 예외로 올린다.
    deadline(초)을 주면 전체가 그 안에 끝나도록 호출마다 남은 시간을 타임아웃으로 쓴다(훅 시간 초과 방지)."""
    started = time.monotonic()

    def left():
        if deadline is None:
            return None
        return max(0.1, min(_timeout(), deadline - (time.monotonic() - started)))

    try:
        listing = http("GET", api_path("documents", bank),
                       query={"limit": limit, "offset": 0, "time_field": "updated_at"},
                       timeout=left())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # 은행은 첫 저장 때 생기므로, 아직 없는 은행은 "기억 없음"이다.
            return "(저장된 기억이 없습니다)"
        raise
    blocks = []
    cut = False
    entries = [e for e in _items(listing) if isinstance(e, dict)]
    # 서버 정렬에만 기대지 않고 updated_at(없으면 created_at) 내림차순으로 한 번 더 정렬한다.
    entries.sort(key=lambda e: str(_first(e, ("updated_at", "created_at")) or ""), reverse=True)
    for entry in entries[:limit]:
        doc_id = _first(entry, ("id", "document_id"))
        if not doc_id:
            continue
        if deadline is not None and deadline - (time.monotonic() - started) < 0.5:
            cut = True
            break
        doc = entry
        try:
            got = http("GET", api_path("document", bank, doc_id), timeout=left())
            if isinstance(got, dict):
                doc = dict(entry, **got)
        except Exception:
            pass
        meta = next((doc[k] for k in ("metadata", "document_metadata") if isinstance(doc.get(k), dict)), {})
        when = _first(meta, ("saved_at",)) or _first(doc, TIME_KEYS) or "?"
        text = _first(doc, TEXT_KEYS)
        if not text:
            # 원문을 받지 못하면 추출 사실로 바꿔치기하지 않는다.
            text = "(원문을 가져오지 못함 — recall 명령으로 검색)"
        blocks.append("[%s] %s · 세션 %s\n%s" % (doc_id, when, _session_of(doc, doc_id), text.strip()))
    if not blocks:
        return "(저장된 기억이 없습니다)"
    if cut:
        blocks.append("…(시간 초과로 나머지 생략. 검색은 recall 명령으로)")
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# 부명령


def err(msg):
    sys.stderr.write(msg + "\n")


def kst_now():
    return datetime.datetime.now().astimezone().replace(microsecond=0)


def cmd_resolve_bank(args):
    bank = current_bank(args.cwd or os.getcwd())
    if not bank:
        return 3
    print(bank)
    return 0


def _need_bank(args):
    bank = current_bank(explicit=args.bank)
    if not bank:
        err("이 폴더에 해당하는 기억 은행이 없습니다.")
    return bank


def cmd_recent(args):
    bank = _need_bank(args)
    if not bank:
        return 3
    try:
        print(fetch_recent(bank, args.limit))
    except Exception as e:
        err("기억 저장소에 닿지 못했습니다 (%s)" % describe_error(e))
    return 0


def cmd_recall(args):
    bank = _need_bank(args)
    if not bank:
        return 3
    body = {"query": args.query, "budget": "low", "max_tokens": 2048}
    try:
        try:
            resp = http("POST", api_path("recall", bank), body=body)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            resp = http("POST", api_path("recall_fallback", bank), body=body)
    except Exception as e:
        err("기억 저장소에 닿지 못했습니다 (%s)" % describe_error(e))
        return 0
    items = _items(resp)
    if not items:
        print("(관련 기억이 없습니다)")
        return 0
    for it in items:
        text = _first(it, TEXT_KEYS) or "?"
        when = _first(it, ("mentioned_at", "occurred_start", "timestamp", "created_at", "updated_at")) or "?"
        doc = _first(it, ("document_id",)) or "?"
        print("- %s (%s, %s)" % (text.strip(), when, doc))
    return 0


def _session_id(args):
    return args.session or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""


def cmd_retain(args):
    bank = _need_bank(args)
    if not bank:
        return 3
    sid = _session_id(args)
    if not token_ok(args.token, sid):
        err("이 명령은 주 세션만 쓸 수 있습니다 (저장 토큰이 맞지 않음).")
        return 4
    try:
        if queued_files():
            fsent, fleft = flush_queue(_env_num("HINDSIGHT_FLUSH_MAX", "3", int),
                                       _env_num("HINDSIGHT_FLUSH_SECONDS", "8", float))
            err("대기열 재전송: %d건 성공, 남은 %d건" % (fsent, fleft))
    except Exception:
        pass
    try:
        if args.file and args.file != "-":
            with open(args.file, encoding="utf-8") as f:
                content = f.read()
        else:
            content = sys.stdin.read()
    except (OSError, UnicodeDecodeError) as e:
        err("본문을 읽지 못했습니다 (%s)" % type(e).__name__)
        return 1
    if not content.strip():
        err("본문이 비어 있습니다.")
        return 1
    now = kst_now()
    doc_id = "%s-%s-%s" % (bank, now.strftime("%Y%m%d-%H%M%S"), sid[:8])
    saved_at = now.isoformat()
    body = {
        "items": [{
            "content": content,
            "timestamp": saved_at,
            "context": KIND_CONTEXT[args.kind],
            "metadata": {
                "cwd": nfc(os.getcwd()),
                "bank": bank,
                "saved_at": saved_at,
                "session_id": sid,
                "kind": args.kind,
            },
            "document_id": doc_id,
            "tags": ["agent:claude-code", "session:%s" % sid, "kind:%s" % args.kind],
        }],
        "async": False,
    }
    try:
        send_retain(bank, body, timeout=_retain_timeout())
    except Exception as e:
        if not is_transient(e):
            err("서버가 저장을 거부했습니다 (%s). 임시 보관하지 않습니다." % describe_error(e))
            return 1
        try:
            path = enqueue(bank, body)
        except OSError as e:
            err("저장도 임시 보관도 실패했습니다 (%s)" % type(e).__name__)
            return 1
        err("기억 저장소에 닿지 못해 임시 보관했습니다: %s" % path)
        return 0
    print(doc_id)
    return 0


def cmd_forget(args):
    bank = _need_bank(args)
    if not bank:
        return 3
    if not token_ok(args.token, _session_id(args)):
        err("이 명령은 주 세션만 쓸 수 있습니다 (저장 토큰이 맞지 않음).")
        return 4
    try:
        http("DELETE", api_path("document", bank, args.doc_id))
    except Exception as e:
        err("삭제하지 못했습니다: %s (%s)" % (args.doc_id, describe_error(e)))
        return 1
    print("삭제했습니다: %s (은행 %s)" % (args.doc_id, bank))
    return 0


def cmd_flush_queue(args):
    sent, left = flush_queue()
    print("재전송 %d건" % sent)
    if left:
        err("남은 대기열 %d건" % left)
    return 0


def _env_num(name, default, cast):
    try:
        return cast(os.environ.get(name, default))
    except ValueError:
        return cast(default)


def build_context(data):
    cwd = data.get("cwd") if isinstance(data, dict) else None
    if not isinstance(cwd, str) or not cwd:
        return ""
    bank = current_bank(cwd)
    if not bank:
        return ""
    lines = ["# 기억 저장소 (Hindsight)", "이 폴더의 은행: %s" % bank]
    hook_start = time.monotonic()
    budget = _env_num("HINDSIGHT_HOOK_SECONDS", "11", float)   # 훅 timeout 15초 안에 끝나도록

    # 토큰은 네트워크보다 먼저 만든다(훅이 끊겨도 토큰 줄은 남도록 출력 순서도 앞에 둔다).
    sid = data.get("session_id")
    if isinstance(sid, str) and sid:
        try:
            lines.append("저장 토큰: %s" % ensure_token(sid))
        except OSError:
            lines.append("저장 토큰: (만들지 못함 — 이번 세션은 저장 불가)")
    else:
        lines.append("저장 토큰: (세션 id 없음 — 이번 세션은 저장 불가)")

    try:
        if queued_files():
            flush_sec = min(_env_num("HINDSIGHT_FLUSH_SECONDS", "6", float), budget * 0.6)
            sent, left = flush_queue(_env_num("HINDSIGHT_FLUSH_MAX", "3", int), flush_sec)
            lines.append("대기열 재전송: %d건 성공, 남은 %d건" % (sent, left))
        else:
            lines.append("대기열: 비어 있음")
    except Exception:
        lines.append("대기열 확인 실패")

    lines.append("")
    lines.append("## 최근 기억")
    try:
        remaining = budget - (time.monotonic() - hook_start)
        recent = fetch_recent(bank, 5, deadline=max(1.0, min(_env_num("HINDSIGHT_RECENT_SECONDS", "4", float), remaining)))
        if len(recent) > RECENT_CAP:
            recent = recent[:RECENT_CAP] + "\n…(이하 생략)"
        lines.append(recent)
    except Exception:
        lines.append("기억 저장소에 닿지 못했습니다")

    lines += [
        "",
        "## 사용법",
        "- 검색: %s recall \"<질의>\"  (질의는 '남은 일' 같은 항목 이름이 아니라 내용 단어로)" % CMD_PATH,
        "- 저장: %s retain --kind note --token <저장 토큰> <파일>  (본문은 파일 또는 stdin)" % CMD_PATH,
        "- 삭제: %s forget <식별자> --token <저장 토큰>" % CMD_PATH,
        "- 중간 저장 기준: 설계를 바꾸는 결정, 되돌리기 어려운 선택, 재현하기 어려운 함정이 나오면 `--kind note`로 저장. 일상 진행 상황은 저장하지 않음.",
        "- \"세션 종료\" 때는 진행 문서를 정리한 뒤 `/memory-save`를 실행한다.",
        "- 저장 금지: 실명, 주민번호, 급여, 연락처, 후보자 정보, 토큰·키·비밀번호는 쓰지 않는다.",
        "- 보조 에이전트는 기억을 저장하지 않으며 토큰을 넘기지 않는다.",
    ]
    if bank in pii_banks():
        lines.append("- 주의: 이 은행(%s)은 개인정보를 다루는 폴더다. 실명·주민번호·급여·연락처·후보자 정보는 요약·가명으로도 저장하지 않는다." % bank)
    return "\n".join(lines) + "\n"


def cmd_session_start_context(args):
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else None
        out = build_context(data) if isinstance(data, dict) else ""
        if out:
            sys.stdout.write(out)
    except Exception:
        pass
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="hindsight-mem", description="Hindsight 기억 저장소 명령")
    sub = p.add_subparsers(dest="cmd")
    sub.required = True

    s = sub.add_parser("resolve-bank", help="폴더의 은행 이름")
    s.add_argument("cwd", nargs="?")
    s.set_defaults(func=cmd_resolve_bank)

    s = sub.add_parser("recent", help="최근 기억")
    s.add_argument("--bank")
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(func=cmd_recent)

    s = sub.add_parser("recall", help="기억 검색")
    s.add_argument("query")
    s.add_argument("--bank")
    s.set_defaults(func=cmd_recall)

    s = sub.add_parser("retain", help="기억 저장(주 세션 전용)")
    s.add_argument("file", nargs="?")
    s.add_argument("--kind", required=True, choices=KINDS)
    s.add_argument("--token", required=True)
    s.add_argument("--bank")
    s.add_argument("--session")
    s.set_defaults(func=cmd_retain)

    s = sub.add_parser("forget", help="기억 삭제(주 세션 전용)")
    s.add_argument("doc_id")
    s.add_argument("--token", required=True)
    s.add_argument("--bank")
    s.add_argument("--session")
    s.set_defaults(func=cmd_forget)

    s = sub.add_parser("flush-queue", help="대기열 재전송")
    s.set_defaults(func=cmd_flush_queue)

    s = sub.add_parser("session-start-context", help="세션 시작 훅 컨텍스트")
    s.set_defaults(func=cmd_session_start_context)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["session-start-context"]:
        try:
            return cmd_session_start_context(None)
        except Exception:
            return 0
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BankError as e:
        err(str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
