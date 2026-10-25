#!/usr/bin/env python3
"""Hindsight 스모크 검증(서버를 새로 올리거나 업그레이드한 뒤).

사용: python3 memory/scripts/smoke.py <base-url>   (예: http://127.0.0.1:8888, SSH 터널)
키: 환경변수 HINDSIGHT_API_KEY, 없으면 ~/.config/hindsight/env 의 HINDSIGHT_API_KEY. 키는 출력하지 않는다.
은행 restore-test에 한국어 인계문 하나를 저장 → 목록·단건·recall 확인 → 삭제.
Python 3.9 표준 라이브러리만 쓴다. 성공하면 종료 0, 어느 단계든 실패하면 단계 이름을 찍고 종료 1.
"""
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BANK = "restore-test"
TIMEOUT = 60
CONTENT = "결정: 기억 저장소는 프로젝트마다 은행 하나. 남은 일: DNS 레코드 추가. 함정: Caddyfile은 제자리 덮어쓰기만."
QUERY = "DNS 남은 일"


class StepFail(Exception):
    pass


def load_key():
    key = os.environ.get("HINDSIGHT_API_KEY")
    if key:
        return key
    path = os.path.expanduser("~/.config/hindsight/env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith("HINDSIGHT_API_KEY="):
                    v = s.split("=", 1)[1].strip()
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                        v = v[1:-1]
                    return v
    except OSError:
        pass
    return None


def call(base, method, path, key=None, body=None, query=None):
    """(상태코드, JSON 또는 None)을 돌려준다. HTTP 오류도 코드로 돌려준다."""
    url = base + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_unredirected_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        return status, json.loads(raw.decode("utf-8")) if raw else None
    except ValueError:
        return status, None


def step(name, ok, detail=""):
    print("[%s] %s%s" % ("OK" if ok else "FAIL", name, (" — " + detail) if detail else ""))
    if not ok:
        raise StepFail(name)


def main(argv):
    if len(argv) != 2:
        print("사용: smoke.py <base-url>")
        return 2
    base = argv[1].rstrip("/")
    key = load_key()
    if not key:
        print("[FAIL] 키 없음 — HINDSIGHT_API_KEY 또는 ~/.config/hindsight/env")
        return 1
    bank_path = "/v1/default/banks/" + BANK
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    doc_id = "restore-test-smoke-" + stamp
    doc_path = bank_path + "/documents/" + urllib.parse.quote(doc_id, safe="")
    summary = []
    try:
        # (a) 키 없이
        s, _ = call(base, "GET", "/health")
        if s == 401:
            print("[OK] a1 키 없이 /health → 401 (인증 켜짐)")
            summary.append("health=인증 필요")
        elif s == 200:
            print("[OK] a1 키 없이 /health → 200 (health는 무인증, 실패 아님)")
            summary.append("health=무인증")
        else:
            step("a1 키 없이 /health", False, "HTTP %s" % s)
        s, _ = call(base, "GET", "/v1/default/banks")
        step("a2 키 없이 /v1/default/banks → 401", s == 401, "HTTP %s" % s)

        # (b) 키 있이
        s, _ = call(base, "GET", "/health", key)
        step("b 키 있이 /health → 200", s == 200, "HTTP %s" % s)

        # (c) retain
        body = {
            "items": [{
                "content": CONTENT,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "context": "스모크 검증 인계문",
                "metadata": {"kind": "smoke", "saved_at": stamp},
                "document_id": doc_id,
                "tags": ["agent:smoke"],
            }],
            "async": False,
        }
        s, r = call(base, "POST", bank_path + "/memories", key, body=body)
        ok = s == 200 and isinstance(r, dict) and r.get("success") is True
        step("c retain %s" % doc_id, ok, "HTTP %s items_count=%s" % (s, (r or {}).get("items_count") if isinstance(r, dict) else None))

        # (d) 목록·단건
        s, r = call(base, "GET", bank_path + "/documents", key,
                    query={"limit": 20, "offset": 0, "time_field": "updated_at"})
        ids = [x.get("id") for x in (r or {}).get("items", [])] if isinstance(r, dict) else []
        step("d1 문서 목록에 document_id 보임", s == 200 and doc_id in ids, "HTTP %s, %d건" % (s, len(ids)))
        s, r = call(base, "GET", doc_path, key)
        step("d2 문서 단건 GET", s == 200 and isinstance(r, dict), "HTTP %s" % s)
        text = r.get("original_text")
        if isinstance(text, str) and text.strip():
            same = text.strip() == CONTENT
            step("d2b 원문이 저장 본문과 동일", same, "키=%s" % sorted(r.keys()))
            summary.append("단건=원문(original_text)")
        else:
            s2, m = call(base, "GET", bank_path + "/memories/list", key, query={"document_id": doc_id, "limit": 50})
            facts = [x.get("text") for x in (m or {}).get("items", [])] if isinstance(m, dict) else []
            step("d3 원문 없음 → 추출 사실 확인", s2 == 200 and bool(facts), "HTTP %s, 사실 %d건" % (s2, len(facts)))
            print("    단건 응답: 원문 없음, 추출 사실만 %d건" % len(facts))
            summary.append("단건=추출 사실만")

        # (e) recall
        s, r = call(base, "POST", bank_path + "/memories/recall", key,
                    body={"query": QUERY, "budget": "low", "max_tokens": 2048})
        results = r.get("results", []) if isinstance(r, dict) else []
        step("e recall \"%s\" 결과 1건 이상" % QUERY, s == 200 and len(results) >= 1, "HTTP %s, %d건" % (s, len(results)))
        for it in results[:3]:
            print("    - %s (%s)" % (str(it.get("text", "?"))[:120], it.get("document_id")))
        summary.append("recall=%d건" % len(results))

        # (e2) MCP 경로도 키 뒤에 있는지, 문서 경로는 막혔는지(Caddy)
        s, _ = call(base, "POST", "/mcp/%s/" % BANK, None, body={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        step("e2 키 없이 /mcp/<은행>/ → 401", s == 401, "HTTP %s" % s)
        if base.startswith("https://"):
            s, _ = call(base, "GET", "/openapi.json", None)
            step("e3 공개 주소에서 /openapi.json 차단(404)", s == 404, "HTTP %s" % s)

        # (f) 삭제
        s, r = call(base, "DELETE", doc_path, key)
        step("f1 문서 삭제", s == 200 and isinstance(r, dict) and r.get("success") is True,
             "HTTP %s memory_units_deleted=%s" % (s, (r or {}).get("memory_units_deleted") if isinstance(r, dict) else None))
        s, r = call(base, "GET", bank_path + "/documents", key, query={"limit": 100, "offset": 0})
        ids = [x.get("id") for x in (r or {}).get("items", [])] if isinstance(r, dict) else []
        step("f2 삭제 후 목록에서 사라짐", s == 200 and doc_id not in ids, "HTTP %s, %d건" % (s, len(ids)))
    except StepFail as e:
        print("스모크 실패: 단계 %s" % e)
        try:
            call(base, "DELETE", doc_path, key)   # 실패해도 샘플은 남기지 않는다
        except Exception:
            pass
        return 1
    except (urllib.error.URLError, OSError) as e:
        print("스모크 실패: 연결 오류 (%s)" % type(e).__name__)
        return 1
    print("스모크 통과: " + ", ".join(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
