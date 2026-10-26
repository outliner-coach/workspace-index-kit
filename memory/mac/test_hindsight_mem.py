"""hindsight_mem.py 단위 테스트.

가짜 작업공간·저장소를 tempfile로 만들고, 모든 경로를 환경변수로 tempdir에 돌린다.
네트워크는 urllib.request.urlopen을 가짜로 바꿔 막는다. 홈 디렉터리는 건드리지 않는다.
"""
import ast
import contextlib
import datetime
import io
import json
import os
import shutil
import socket
import tempfile
import time
import unicodedata
import unittest
import urllib.error
import urllib.request
from unittest import mock

from memory.mac import hindsight_mem as hm


def card(name, path, covers=(), sensitive="none"):
    return (
        "---\n"
        "name: %s\n"
        "path: %s\n"
        "group: 업무\n"
        "status: active\n"
        "summary: 테스트 카드\n"
        "stack: []\n"
        "entry_docs: []\n"
        "covers: [%s]\n"
        "related: []\n"
        "aliases: []\n"
        "sensitive: %s\n"
        "read_scope: []\n"
        "last_activity: 2026-10-01\n"
        "last_verified: 2026-10-01\n"
        "---\n"
        "## 한눈에\n"
    ) % (name, path, ", ".join(covers), sensitive)


class FakeResp(object):
    def __init__(self, payload, status=200):
        self._data = json.dumps(payload).encode("utf-8") if payload is not None else b""
        self.status = status

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = os.path.join(self.tmp, "ws")
        self.repo = os.path.join(self.ws, "workspace-index")
        cat = os.path.join(self.repo, "catalog")
        os.makedirs(cat)
        cards = {
            "alpha": card("alpha", "alpha", ["alpha-old", "alpha-wt"]),
            "outer": card("outer", "work/outer"),
            "inner": card("inner", "work/outer/inner"),
            "ledger": card("ledger", "work/가계부"),
            "crm": card("crm", "work/Clients", sensitive="pii"),
        }
        for name, text in cards.items():
            with open(os.path.join(cat, name + ".md"), "w", encoding="utf-8") as f:
                f.write(text)
        self.envfile = os.path.join(self.tmp, "env")
        with open(self.envfile, "w", encoding="utf-8") as f:
            f.write("HINDSIGHT_API_KEY=test-key-value\nHINDSIGHT_URL=https://example.invalid\n")
        self.queue = os.path.join(self.tmp, "queue")
        self.state = os.path.join(self.tmp, "state")
        env = {
            "HOME": os.path.join(self.tmp, "home"),
            "HINDSIGHT_REPO_ROOT": self.repo,
            "HINDSIGHT_WORKSPACE_ROOT": self.ws,
            "HINDSIGHT_QUEUE_DIR": self.queue,
            "HINDSIGHT_STATE_DIR": self.state,
            "HINDSIGHT_ENV_FILE": self.envfile,
        }
        self.envpatch = mock.patch.dict(os.environ, env)
        self.envpatch.start()
        for k in ("HINDSIGHT_API_KEY", "HINDSIGHT_URL", "HINDSIGHT_BANK_OVERRIDE",
                  "CLAUDE_CODE_SESSION_ID", "HINDSIGHT_TIMEOUT", "HINDSIGHT_FLUSH_MAX",
                  "HINDSIGHT_FLUSH_SECONDS", "WORKSPACE_INDEX_MACHINE"):
            os.environ.pop(k, None)
        self.requests = []
        self.timeouts = []
        self.responder = lambda req: FakeResp({"success": True})
        self.urlpatch = mock.patch.object(urllib.request, "urlopen", self.fake_urlopen)
        self.urlpatch.start()

    def tearDown(self):
        self.urlpatch.stop()
        self.envpatch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_urlopen(self, req, timeout=None):
        self.requests.append(req)
        self.timeouts.append(timeout)
        return self.responder(req)

    def run_main(self, argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = hm.main(argv)
        return code, out.getvalue(), err.getvalue()

    def p(self, *parts):
        return os.path.join(self.ws, *parts)


class ResolveTest(Base):
    def test_root_is_workspace_index(self):
        self.assertEqual(hm.resolve_bank(self.ws), "workspace-index")

    def test_repo_subdir_is_workspace_index(self):
        self.assertEqual(hm.resolve_bank(os.path.join(self.repo, "memory", "mac")), "workspace-index")

    def test_card_path_deepest_wins(self):
        self.assertEqual(hm.resolve_bank(self.p("work", "outer")), "outer")
        self.assertEqual(hm.resolve_bank(self.p("work", "outer", "x")), "outer")
        self.assertEqual(hm.resolve_bank(self.p("work", "outer", "inner", "src")), "inner")
        self.assertEqual(hm.resolve_bank(self.p("alpha")), "alpha")

    def test_prefix_is_not_parent(self):
        # alpha-old는 covers로 alpha에 가야 하고, 'alpha' 접두사 때문에 맞으면 안 된다
        self.assertEqual(hm.resolve_bank(self.p("alpha-old", "sub")), "alpha")
        self.assertIsNone(hm.resolve_bank(self.p("alphabet")))

    def test_covers_maps_to_canonical(self):
        self.assertEqual(hm.resolve_bank(self.p("alpha-wt")), "alpha")

    def test_no_card_is_none_and_exit3(self):
        self.assertIsNone(hm.resolve_bank(self.p("test")))
        self.assertIsNone(hm.resolve_bank(os.path.join(self.tmp, "elsewhere")))
        code, out, _ = self.run_main(["resolve-bank", self.p("test")])
        self.assertEqual((code, out), (3, ""))

    def test_resolve_bank_cli(self):
        code, out, _ = self.run_main(["resolve-bank", self.p("alpha", "src")])
        self.assertEqual((code, out.strip()), (0, "alpha"))

    def test_nfd_cwd_matches_nfc_card(self):
        nfd = unicodedata.normalize("NFD", self.p("work", "가계부", "v2"))
        self.assertNotEqual(nfd, unicodedata.normalize("NFC", nfd))
        self.assertEqual(hm.resolve_bank(nfd), "ledger")


class MachineResolveTest(Base):
    """다른 맥: machines/<맥>.md 위치표가 카드 path·covers 대신 쓰인다(같은 카드 = 같은 은행)."""

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.repo, "machines"))
        with open(os.path.join(self.repo, "machines", "other.md"), "w", encoding="utf-8") as f:
            f.write("# 맥 other\n\n| 카드 | 경로 |\n|---|---|\n| alpha | my-alpha |\n"
                    "| ledger | 가계부 |\n\n| 폴더 | 정본 카드 |\n|---|---|\n| alpha-copy | alpha |\n"
                    "\n| 폴더 | 사유 |\n|---|---|\n| work | 묶음 |\n")
        os.environ["WORKSPACE_INDEX_MACHINE"] = "other"

    def tearDown(self):
        os.environ.pop("WORKSPACE_INDEX_MACHINE", None)
        super().tearDown()

    def test_table_paths_used(self):
        self.assertEqual(hm.resolve_bank(self.p("my-alpha", "src")), "alpha")
        self.assertEqual(hm.resolve_bank(self.p("alpha-copy")), "alpha")
        nfd = unicodedata.normalize("NFD", self.p("가계부"))
        self.assertEqual(hm.resolve_bank(nfd), "ledger")

    def test_card_paths_ignored(self):
        # 기본 맥의 path·covers는 이 맥에서 쓰지 않는다
        self.assertIsNone(hm.resolve_bank(self.p("alpha")))
        self.assertIsNone(hm.resolve_bank(self.p("alpha-old")))
        self.assertIsNone(hm.resolve_bank(self.p("work", "outer")))
        self.assertEqual(hm.resolve_bank(self.ws), "workspace-index")

    def test_banks_still_all_cards(self):
        self.assertIn("outer", hm.allowed_banks())

    def test_without_table_falls_back(self):
        os.environ["WORKSPACE_INDEX_MACHINE"] = "third"
        self.assertEqual(hm.resolve_bank(self.p("alpha")), "alpha")

    def test_dash_path_is_not_here(self):
        os.environ["WORKSPACE_INDEX_MACHINE"] = "third"
        with open(os.path.join(self.repo, "catalog", "only.md"), "w", encoding="utf-8") as f:
            f.write(card("only", "-"))
        self.assertIsNone(hm.resolve_bank(self.p("-")))
        self.assertIn("only", hm.allowed_banks())

class AllowlistTest(Base):
    def test_unknown_bank_rejected(self):
        code, out, err = self.run_main(["recent", "--bank", "nope"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertTrue(err.strip())
        self.assertEqual(self.requests, [])

    def test_reserved_names_allowed(self):
        allowed = hm.allowed_banks()
        self.assertIn("workspace-index", allowed)
        self.assertIn("restore-test", allowed)
        self.assertIn("alpha", allowed)
        self.responder = lambda req: FakeResp({"items": []})
        for b in ("workspace-index", "restore-test"):
            code, _, _ = self.run_main(["recent", "--bank", b])
            self.assertEqual(code, 0)

    def test_override_env_checked(self):
        os.environ["HINDSIGHT_BANK_OVERRIDE"] = "restore-test"
        code, out, _ = self.run_main(["resolve-bank", self.p("test")])
        self.assertEqual((code, out.strip()), (0, "restore-test"))
        os.environ["HINDSIGHT_BANK_OVERRIDE"] = "bogus"
        code, _, _ = self.run_main(["resolve-bank", self.p("alpha")])
        self.assertEqual(code, 2)


class ConfigTest(Base):
    def test_parse_quotes_and_comments(self):
        with open(self.envfile, "w", encoding="utf-8") as f:
            f.write("# 주석\n\nHINDSIGHT_API_KEY=\"quoted-key\"\n  # 들여쓴 주석\n"
                    "HINDSIGHT_URL='https://x.invalid/'\nOTHER = v \n")
        cfg = hm.load_config()
        self.assertEqual(cfg["HINDSIGHT_API_KEY"], "quoted-key")
        self.assertEqual(cfg["HINDSIGHT_URL"], "https://x.invalid")

    def test_env_overrides_file(self):
        os.environ["HINDSIGHT_URL"] = "https://env.invalid"
        cfg = hm.load_config()
        self.assertEqual(cfg["HINDSIGHT_URL"], "https://env.invalid")
        self.assertEqual(cfg["HINDSIGHT_API_KEY"], "test-key-value")

    def test_default_url(self):
        os.remove(self.envfile)
        self.assertEqual(hm.load_config()["HINDSIGHT_URL"], "")

    def test_missing_url_is_error_not_request(self):
        with open(self.envfile, "w", encoding="utf-8") as f:
            f.write("HINDSIGHT_API_KEY=test-key-value\n")
        with self.assertRaises(RuntimeError):
            hm.http("GET", "/health")


class TokenRetainTest(Base):
    SID = "11111111-2222-3333-4444-555555555555"

    def start_session(self):
        payload = json.dumps({"cwd": self.p("alpha"), "session_id": self.SID})
        self.responder = lambda req: FakeResp({"items": []})
        code, out, _ = self.run_main(["session-start-context"], stdin=payload)
        self.assertEqual(code, 0)
        path = os.path.join(self.state, "session-%s.token" % self.SID)
        with open(path, encoding="utf-8") as f:
            return f.read().strip()

    def test_mismatch_exit4(self):
        self.start_session()
        os.environ["CLAUDE_CODE_SESSION_ID"] = self.SID
        self.requests = []
        code, out, err = self.run_main(
            ["retain", "--kind", "note", "--token", "wrong", "--bank", "alpha"], stdin="본문")
        self.assertEqual(code, 4)
        self.assertIn("주 세션", err)
        self.assertEqual(self.requests, [])

    def test_no_session_file_exit4(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "unknown-session"
        code, _, _ = self.run_main(
            ["retain", "--kind", "note", "--token", "x", "--bank", "alpha"], stdin="본문")
        self.assertEqual(code, 4)

    def test_match_sends_retain(self):
        token = self.start_session()
        os.environ["CLAUDE_CODE_SESSION_ID"] = self.SID
        self.requests = []
        self.timeouts = []
        self.responder = lambda req: FakeResp({"success": True})
        code, out, err = self.run_main(
            ["retain", "--kind", "handoff", "--token", token, "--bank", "alpha"], stdin="인계 본문")
        self.assertEqual(code, 0, err)
        self.assertEqual(len([r for r in self.requests if r.full_url.endswith("/memories")]), 1)
        req = self.requests[0]
        self.assertEqual(req.get_method(), "POST")
        self.assertTrue(req.full_url.endswith("/v1/default/banks/alpha/memories"))
        self.assertEqual(req.get_header("Authorization"), "Bearer test-key-value")
        body = json.loads(req.data.decode("utf-8"))
        item = body["items"][0]
        self.assertFalse(body["async"])
        self.assertEqual(item["content"], "인계 본문")
        self.assertIn("kind:handoff", item["tags"])
        self.assertIn("session:%s" % self.SID, item["tags"])
        self.assertIn("agent:claude-code", item["tags"])
        self.assertTrue(item["document_id"].startswith("alpha-"))
        self.assertTrue(item["document_id"].endswith("-11111111"))
        self.assertRegex(item["timestamp"], r"[+-]\d\d:\d\d$")
        self.assertTrue(all(isinstance(v, str) for v in item["metadata"].values()))
        self.assertEqual(out.strip(), item["document_id"])
        self.assertNotIn("test-key-value", out + err)

    def test_session_flag_overrides_env(self):
        token = self.start_session()
        os.environ["CLAUDE_CODE_SESSION_ID"] = "other"
        code, _, _ = self.run_main(
            ["retain", "--kind", "note", "--token", token, "--bank", "alpha",
             "--session", self.SID], stdin="x")
        self.assertEqual(code, 0)

    def test_forget(self):
        token = self.start_session()
        os.environ["CLAUDE_CODE_SESSION_ID"] = self.SID
        self.requests = []
        code, out, _ = self.run_main(["forget", "alpha-20261016-101010-11111111",
                                      "--token", token, "--bank", "alpha"])
        self.assertEqual(code, 0)
        self.assertEqual(self.requests[0].get_method(), "DELETE")
        self.assertIn("/documents/alpha-20261016-101010-11111111", self.requests[0].full_url)
        self.assertIn("alpha-20261016-101010-11111111", out)

        def boom(req):
            raise urllib.error.URLError("down")
        self.responder = boom
        code, _, err = self.run_main(["forget", "x", "--token", token, "--bank", "alpha"])
        self.assertEqual(code, 1)
        self.assertEqual(len(err.strip().splitlines()), 1)


class QueueTest(Base):
    SID = "abcdefgh-0000"

    def setUp(self):
        super(QueueTest, self).setUp()
        os.makedirs(self.state)
        with open(os.path.join(self.state, "session-%s.token" % self.SID), "w") as f:
            f.write("tok")
        os.environ["CLAUDE_CODE_SESSION_ID"] = self.SID

    def queued(self):
        if not os.path.isdir(self.queue):
            return []
        return sorted(os.listdir(self.queue))

    def fail(self, req):
        raise urllib.error.URLError("connection refused")

    def test_failure_queues_and_exit0(self):
        self.responder = self.fail
        code, out, err = self.run_main(
            ["retain", "--kind", "note", "--token", "tok", "--bank", "alpha"], stdin="본문")
        self.assertEqual(code, 0)
        files = self.queued()
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].endswith("-alpha.json"))
        self.assertIn("임시 보관", err)
        self.assertEqual(len(err.strip().splitlines()), 1)
        with open(os.path.join(self.queue, files[0]), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["bank"], "alpha")
        self.assertEqual(data["body"]["items"][0]["content"], "본문")

    def test_http_error_queues(self):
        def unauthorized(req):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", None, None)
        self.responder = unauthorized
        code, _, _ = self.run_main(
            ["retain", "--kind", "note", "--token", "tok", "--bank", "alpha"], stdin="본문")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.queued()), 1)

    def make_queue(self, n):
        for i in range(n):
            hm.enqueue("alpha", {"items": [{"content": "c%d" % i}], "async": False},
                       stamp="20261016T0000%02dZ" % i)
        self.assertEqual(len(self.queued()), n)

    def test_flush_success_removes(self):
        self.make_queue(2)
        self.responder = lambda req: FakeResp({"success": True})
        code, out, _ = self.run_main(["flush-queue"])
        self.assertEqual(code, 0)
        self.assertEqual(self.queued(), [])
        self.assertIn("2", out)
        sent = [json.loads(r.data.decode("utf-8"))["items"][0]["content"] for r in self.requests]
        self.assertEqual(sent, ["c0", "c1"])

    def test_permanent_reject_not_queued(self):
        self.responder = lambda req: (_ for _ in ()).throw(
            urllib.error.HTTPError(req.full_url, 422, "Unprocessable", {}, None))
        code, out, err = self.run_main(
            ["retain", "--kind", "note", "--token", "tok", "--bank", "alpha"], stdin="본문")
        self.assertEqual(code, 1)
        self.assertEqual(self.queued(), [])
        self.assertIn("거부", err)

    def test_flush_parks_permanent_reject_and_stops(self):
        self.make_queue(2)
        self.responder = lambda req: (_ for _ in ()).throw(
            urllib.error.HTTPError(req.full_url, 422, "Unprocessable", {}, None))
        code, out, err = self.run_main(["flush-queue"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len([f for f in self.queued() if f.endswith(".json")]), 1)
        self.assertEqual(len(os.listdir(os.path.join(self.queue, "failed"))), 1)

    def test_retain_flushes_queue_first(self):
        self.make_queue(1)
        self.responder = lambda req: FakeResp({"success": True})
        code, out, err = self.run_main(
            ["retain", "--kind", "note", "--token", "tok", "--bank", "alpha"], stdin="본문")
        self.assertEqual(code, 0)
        self.assertEqual(len([r for r in self.requests if r.full_url.endswith("/memories")]), 2)
        self.assertIn("재전송", err)
        self.assertEqual([f for f in self.queued() if f.endswith(".json")], [])

    def test_flush_stops_on_failure(self):
        self.make_queue(3)
        self.responder = self.fail
        code, out, err = self.run_main(["flush-queue"])
        self.assertEqual(len(self.queued()), 3)
        self.assertEqual(len(self.requests), 1)
        self.assertIn("3", out + err)

    def test_hook_flush_limit(self):
        self.make_queue(5)
        self.responder = lambda req: FakeResp({"success": True, "items": []})
        payload = json.dumps({"cwd": self.p("alpha"), "session_id": "s1"})
        code, out, _ = self.run_main(["session-start-context"], stdin=payload)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.queued()), 2)


class SessionStartTest(Base):
    def test_no_bank_empty(self):
        payload = json.dumps({"cwd": self.p("test"), "session_id": "s1"})
        code, out, err = self.run_main(["session-start-context"], stdin=payload)
        self.assertEqual((code, out), (0, ""))
        self.assertFalse(os.path.exists(os.path.join(self.state, "session-s1.token")))

    def test_broken_stdin_empty(self):
        code, out, _ = self.run_main(["session-start-context"], stdin="{not json")
        self.assertEqual((code, out), (0, ""))
        code, out, _ = self.run_main(["session-start-context"], stdin="")
        self.assertEqual((code, out), (0, ""))

    def test_bank_context(self):
        self.responder = lambda req: FakeResp({"items": []})
        payload = json.dumps({"cwd": self.p("alpha", "src"), "session_id": "s1"})
        code, out, _ = self.run_main(["session-start-context"], stdin=payload)
        self.assertEqual(code, 0)
        path = os.path.join(self.state, "session-s1.token")
        self.assertTrue(os.path.exists(path))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with open(path) as f:
            token = f.read().strip()
        self.assertIn("저장 토큰: " + token, out)
        self.assertIn("alpha", out)
        self.assertIn("~/.claude/scripts/hindsight-mem", out)
        self.assertNotIn("test-key-value", out)
        # 두 번째 호출은 토큰 재사용
        code, out2, _ = self.run_main(["session-start-context"], stdin=payload)
        self.assertIn("저장 토큰: " + token, out2)

    def test_token_never_starts_with_dash(self):
        seq = iter(["-dashfirst", "okToken"])
        with mock.patch.object(hm.secrets, "token_urlsafe", lambda n: next(seq)):
            self.assertEqual(hm.ensure_token("s9"), "okToken")

    def test_server_down_line(self):
        def boom(req):
            raise urllib.error.URLError("down")
        self.responder = boom
        payload = json.dumps({"cwd": self.p("alpha"), "session_id": "s2"})
        code, out, _ = self.run_main(["session-start-context"], stdin=payload)
        self.assertEqual(code, 0)
        self.assertIn("기억 저장소에 닿지 못했습니다", out)
        self.assertIn("저장 토큰:", out)

    def test_pii_bank_extra_warning(self):
        self.responder = lambda req: FakeResp({"items": []})
        base = json.dumps({"cwd": self.p("alpha"), "session_id": "s3"})
        _, normal, _ = self.run_main(["session-start-context"], stdin=base)
        pii = json.dumps({"cwd": self.p("work", "Clients"), "session_id": "s4"})
        _, strict, _ = self.run_main(["session-start-context"], stdin=pii)
        self.assertGreater(strict.count("후보자"), normal.count("후보자"))

    def test_recent_items_rendered_and_capped(self):
        long_text = "가" * 6000

        def responder(req):
            url = req.full_url
            if "/documents?" in url:
                return FakeResp({"items": [{"id": "alpha-1"}, {"id": "alpha-2"}]})
            if url.endswith("/documents/alpha-1"):
                return FakeResp({"id": "alpha-1", "original_text": long_text,
                                 "created_at": "2026-10-16T10:00:00+09:00",
                                 "tags": ["session:abcdef123456"]})
            if url.endswith("/documents/alpha-2"):
                return FakeResp({"id": "alpha-2"})
            if "/memories/list" in url:
                return FakeResp({"items": [{"text": "추출 사실 하나"}]})
            return FakeResp({})
        self.responder = responder
        payload = json.dumps({"cwd": self.p("alpha"), "session_id": "s5"})
        code, out, _ = self.run_main(["session-start-context"], stdin=payload)
        self.assertEqual(code, 0)
        self.assertIn("alpha-1", out)
        self.assertIn("원문을 가져오지 못함", out)
        self.assertNotIn("추출 사실 하나", out)   # 원문이 없으면 추출 사실로 바꿔치기하지 않는다
        code, out, _ = self.run_main(["recent", "--bank", "alpha"])
        self.assertIn("가" * 100, out)


class RecallTest(Base):
    def test_recall_retries_on_404(self):
        def responder(req):
            if req.full_url.endswith("/memories/recall"):
                raise urllib.error.HTTPError(req.full_url, 404, "nf", None, None)
            return FakeResp({"results": [{"text": "사실", "document_id": "alpha-9",
                                          "mentioned_at": "2026-10-16"}]})
        self.responder = responder
        code, out, _ = self.run_main(["recall", "질의", "--bank", "alpha"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.requests), 2)
        self.assertTrue(self.requests[1].full_url.endswith("/banks/alpha/recall"))
        self.assertIn("사실", out)
        self.assertIn("alpha-9", out)

    def test_recall_failure_exit0(self):
        def boom(req):
            raise urllib.error.URLError("down")
        self.responder = boom
        code, out, err = self.run_main(["recall", "질의", "--bank", "alpha"])
        self.assertEqual(code, 0)
        self.assertEqual(len(err.strip().splitlines()), 1)


def http_err(req, code):
    raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)


MM = "/mental-models/project-summary"


class SummaryBase(Base):
    SID = "abcdefgh-1111"

    def setUp(self):
        super(SummaryBase, self).setUp()
        os.makedirs(self.state)
        with open(os.path.join(self.state, "session-%s.token" % self.SID), "w") as f:
            f.write("tok")
        os.environ["CLAUDE_CODE_SESSION_ID"] = self.SID

    def calls(self):
        return [(r.get_method(), r.full_url.split("example.invalid", 1)[1]) for r in self.requests]


class RetainSummaryTest(SummaryBase):
    def retain(self, bank="alpha"):
        return self.run_main(["retain", "--kind", "note", "--token", "tok", "--bank", bank], stdin="본문")

    def test_creates_when_missing(self):
        def responder(req):
            if req.get_method() == "GET" and req.full_url.endswith(MM):
                http_err(req, 404)
            if req.full_url.endswith("/mental-models"):
                return FakeResp({"mental_model_id": "project-summary", "operation_id": "op"})
            return FakeResp({"success": True})
        self.responder = responder
        code, out, err = self.retain()
        self.assertEqual(code, 0, err)
        self.assertTrue(out.strip().startswith("alpha-"))
        posts = [r for r in self.requests if r.full_url.endswith("/banks/alpha/mental-models")]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].get_method(), "POST")
        body = json.loads(posts[0].data.decode("utf-8"))
        self.assertEqual(body["id"], "project-summary")
        self.assertEqual(body["trigger"], {"refresh_after_consolidation": True, "mode": "full"})
        self.assertEqual(body["max_tokens"], 800)
        self.assertIn("한국어", body["source_query"])
        self.assertTrue(body["name"])
        self.assertEqual(err, "")

    def test_skips_when_present(self):
        self.responder = lambda req: FakeResp({"success": True, "id": "project-summary", "content": "x"})
        code, out, err = self.retain()
        self.assertEqual(code, 0)
        self.assertNotIn(("POST", "/v1/default/banks/alpha/mental-models"), self.calls())
        self.assertIn(("GET", "/v1/default/banks/alpha" + MM), self.calls())

    def test_create_failure_keeps_retain_result(self):
        def responder(req):
            if "/mental-models" in req.full_url:
                http_err(req, 500)
            return FakeResp({"success": True})
        self.responder = responder
        code, out, err = self.retain()
        self.assertEqual(code, 0)
        self.assertTrue(out.strip().startswith("alpha-"))
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("요약", err)

    def test_restore_test_bank_no_summary(self):
        self.responder = lambda req: FakeResp({"success": True})
        code, _, _ = self.retain("restore-test")
        self.assertEqual(code, 0)
        self.assertFalse(any("/mental-models" in u for _, u in self.calls()))

    def test_queued_retain_and_flush_do_not_create(self):
        def down(req):
            raise urllib.error.URLError("down")
        self.responder = down
        self.retain()
        self.requests = []
        self.responder = lambda req: FakeResp({"success": True})
        code, _, _ = self.run_main(["flush-queue"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(any("/mental-models" in u for _, u in self.calls()))

    def test_hook_flush_does_not_create(self):
        hm.enqueue("alpha", {"items": [{"content": "c"}], "async": False}, stamp="20261016T000000Z")
        self.responder = lambda req: FakeResp({"success": True, "items": []})
        payload = json.dumps({"cwd": self.p("alpha"), "session_id": "s1"})
        self.run_main(["session-start-context"], stdin=payload)
        self.assertFalse(any(m == "POST" and "/mental-models" in u for m, u in self.calls()))


class ForgetSummaryTest(SummaryBase):
    def forget(self):
        return self.run_main(["forget", "alpha-1", "--token", "tok", "--bank", "alpha"])

    def test_clear_then_refresh(self):
        self.responder = lambda req: FakeResp({"success": True, "id": "project-summary"})
        code, out, err = self.forget()
        self.assertEqual(code, 0)
        calls = self.calls()
        self.assertEqual(calls[0][0], "DELETE")
        mm_posts = [u for m, u in calls if m == "POST"]
        self.assertEqual(mm_posts, ["/v1/default/banks/alpha" + MM + "/clear",
                                    "/v1/default/banks/alpha" + MM + "/refresh"])
        self.assertNotIn("남아 있을 수", out)
        self.assertIn("삭제했습니다", out)

    def test_refresh_failure_notice(self):
        def responder(req):
            if req.full_url.endswith("/refresh"):
                http_err(req, 500)
            return FakeResp({"success": True})
        self.responder = responder
        code, out, err = self.forget()
        self.assertEqual(code, 0)
        self.assertIn("요약에 지운 내용이 남아 있을 수 있음(다음 갱신 때 사라짐)", out)

    def test_no_summary_no_calls(self):
        def responder(req):
            if req.get_method() == "GET" and req.full_url.endswith(MM):
                http_err(req, 404)
            return FakeResp({"success": True})
        self.responder = responder
        code, out, err = self.forget()
        self.assertEqual(code, 0)
        self.assertFalse(any(m == "POST" for m, _ in self.calls()))
        self.assertNotIn("남아 있을 수", out + err)

    def test_forget_failure_no_summary_calls(self):
        def responder(req):
            if req.get_method() == "DELETE":
                http_err(req, 500)
            return FakeResp({"success": True})
        self.responder = responder
        code, _, _ = self.forget()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.requests), 1)


class SummarySyncTest(SummaryBase):
    def setUp(self):
        super(SummarySyncTest, self).setUp()
        text = card("gone", "gone").replace("status: active", "status: removed")
        with open(os.path.join(self.repo, "catalog", "gone.md"), "w", encoding="utf-8") as f:
            f.write(text)
        self.server_banks = ["alpha", "inner", "gone", "workspace-index", "restore-test", "stranger", "outer"]
        self.docs = {"alpha": 2, "inner": 0, "gone": 1, "workspace-index": 3, "restore-test": 5,
                     "stranger": 4, "outer": 1}
        self.existing = {"outer"}
        self.fail_banks = set()

        def responder(req):
            path = req.full_url.split("example.invalid", 1)[1].split("?")[0]
            m = req.get_method()
            if path == "/v1/default/banks":
                return FakeResp({"banks": [{"bank_id": b} for b in self.server_banks],
                                 "total": len(self.server_banks)})
            parts = path.split("/")
            bank = parts[4]
            if path.endswith("/documents"):
                n = self.docs[bank]
                return FakeResp({"items": [{"id": "d"}] * min(n, 1), "total": n})
            if bank in self.fail_banks and "/mental-models" in path:
                http_err(req, 500)
            if m == "GET" and path.endswith(MM):
                if bank in self.existing:
                    return FakeResp({"id": "project-summary", "content": ""})
                http_err(req, 404)
            if m == "POST" and path.endswith("/mental-models"):
                return FakeResp({"mental_model_id": "project-summary", "operation_id": "o"})
            return FakeResp({})
        self.responder = responder

    def created(self):
        out = []
        for r in self.requests:
            if r.get_method() == "POST" and r.full_url.endswith("/mental-models"):
                out.append(r.full_url.split("/banks/")[1].split("/")[0])
        return sorted(out)

    def test_targets(self):
        code, out, err = self.run_main(["summary-sync", "--token", "tok"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.created(), ["alpha", "gone", "workspace-index"])
        touched = set(u.split("/banks/")[1].split("/")[0] for _, u in self.calls() if "/banks/" in u)
        self.assertNotIn("restore-test", touched)
        self.assertNotIn("stranger", touched)
        self.assertIn("outer", out)
        self.assertIn("이미 있음", out)
        self.assertIn("생성", out)
        self.assertNotIn("inner", out)

    def test_requires_token(self):
        code, out, err = self.run_main(["summary-sync", "--token", "wrong"])
        self.assertEqual(code, 4)
        self.assertEqual(self.requests, [])
        os.environ["CLAUDE_CODE_SESSION_ID"] = "other"
        code, _, _ = self.run_main(["summary-sync", "--token", "tok"])
        self.assertEqual(code, 4)
        code, _, _ = self.run_main(["summary-sync", "--token", "tok", "--session", self.SID])
        self.assertEqual(code, 0)

    def test_failure_continues_and_exit1(self):
        self.fail_banks = {"alpha"}
        code, out, err = self.run_main(["summary-sync", "--token", "tok"])
        self.assertEqual(code, 1)
        self.assertEqual(self.created(), ["gone", "workspace-index"])
        self.assertIn("실패", out)

    def test_bank_list_failure_exit1(self):
        def down(req):
            raise urllib.error.URLError("down")
        self.responder = down
        code, _, err = self.run_main(["summary-sync", "--token", "tok"])
        self.assertEqual(code, 1)
        self.assertTrue(err.strip())


class StartSummaryTest(Base):
    def setUp(self):
        super(StartSummaryTest, self).setUp()
        self.mm = {"id": "project-summary", "content": "## 상태\n요약 본문입니다",
                   "last_refreshed_at": "2026-10-23T03:04:05.123456Z"}
        self.mm_error = None
        self.n_docs = 5

        def responder(req):
            url = req.full_url
            if url.endswith(MM):
                if self.mm_error is not None:
                    raise self.mm_error
                return FakeResp(self.mm)
            if "/documents?" in url:
                return FakeResp({"items": [{"id": "alpha-%d" % i,
                                            "updated_at": "2026-10-1%dT00:00:00Z" % i}
                                           for i in range(self.n_docs)]})
            if "/documents/" in url:
                did = url.rsplit("/", 1)[1]
                return FakeResp({"id": did, "original_text": "본문 " + did})
            return FakeResp({})
        self.responder = responder

    def ctx(self):
        payload = json.dumps({"cwd": self.p("alpha"), "session_id": "s1"})
        code, out, _ = self.run_main(["session-start-context"], stdin=payload)
        self.assertEqual(code, 0)
        return out

    def test_order_and_header(self):
        out = self.ctx()
        i_head = out.index("이 폴더의 은행")
        i_sum = out.index("## 프로젝트 요약")
        i_recent = out.index("## 최근 기억")
        i_use = out.index("## 사용법")
        self.assertTrue(i_head < i_sum < i_recent < i_use)
        when = datetime.datetime(2026, 10, 23, 3, 4, 5, tzinfo=datetime.timezone.utc).astimezone()
        self.assertIn("## 프로젝트 요약 (자동 생성 · 갱신 %s)" % when.strftime("%Y-%m-%d %H:%M"), out)
        self.assertIn("언어모델이 기억에서 만든 요약이다. 최근 기억이나 프로젝트 파일과 어긋나면 그쪽을 따른다.", out)
        self.assertIn("요약 본문입니다", out)
        self.assertLess(out.index("요약 본문입니다"), i_recent)

    def test_header_uses_local_time_zone(self):
        # UTC 가 아닌 시간대로 고정해 현지 시각 변환을 확인한다(2026-10-23 03:04Z = 태평양 여름시간 10-22 20:04).
        old = os.environ.get("TZ")
        os.environ["TZ"] = "America/Los_Angeles"
        time.tzset()
        try:
            out = self.ctx()
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()
        self.assertIn("## 프로젝트 요약 (자동 생성 · 갱신 2026-10-22 20:04)", out)

    def test_header_without_time(self):
        del self.mm["last_refreshed_at"]
        out = self.ctx()
        self.assertIn("## 프로젝트 요약 (자동 생성)\n", out)

    def test_body_headings_demoted(self):
        self.mm["content"] = "# 큰 제목\n## 상태\n### 세부\n#### 그대로\n#해시태그 아님"
        out = self.ctx()
        self.assertNotIn("\n## 상태", out)
        self.assertIn("#### 큰 제목\n#### 상태\n#### 세부\n#### 그대로\n#해시태그 아님", out)

    def test_body_cap(self):
        self.mm["content"] = "나" * 5000
        out = self.ctx()
        self.assertIn("나" * 4000, out)
        self.assertNotIn("나" * 4001, out)
        self.assertIn("요약이 길어", out)

    def test_recent_three(self):
        out = self.ctx()
        listing = [r for r in self.requests if "/documents?" in r.full_url]
        self.assertIn("limit=3", listing[0].full_url)
        self.assertEqual(sum(1 for r in self.requests if "/documents/" in r.full_url), 3)
        self.assertIn("[alpha-4]", out)
        self.assertIn("[alpha-2]", out)
        self.assertNotIn("[alpha-1]", out)

    def assert_omitted(self, out):
        self.assertNotIn("프로젝트 요약", out)
        self.assertNotIn("언어모델이", out)
        self.assertIn("## 최근 기억", out)
        self.assertIn("## 사용법", out)

    def test_404_omitted(self):
        self.mm_error = urllib.error.HTTPError("u", 404, "nf", {}, None)
        self.assert_omitted(self.ctx())

    def test_empty_content_omitted(self):
        self.mm["content"] = "  "
        self.assert_omitted(self.ctx())

    def test_timeout_omitted(self):
        self.mm_error = socket.timeout("timed out")
        self.assert_omitted(self.ctx())

    def test_summary_timeout_bounded(self):
        self.ctx()
        idx = [i for i, r in enumerate(self.requests) if r.full_url.endswith(MM)]
        self.assertEqual(len(idx), 1)
        self.assertLessEqual(self.timeouts[idx[0]], 3.0)

    def test_skip_when_budget_short(self):
        os.environ["HINDSIGHT_HOOK_SECONDS"] = "4.5"
        try:
            out = self.ctx()
        finally:
            os.environ.pop("HINDSIGHT_HOOK_SECONDS", None)
        self.assertFalse(any(r.full_url.endswith(MM) for r in self.requests))
        self.assertIn("## 최근 기억", out)
        self.assertIn("본문 alpha-4", out)

    def test_unexpected_error_exit0(self):
        self.mm_error = ValueError("boom")
        self.assert_omitted(self.ctx())


class SyntaxTest(unittest.TestCase):
    def test_python39_syntax(self):
        here = os.path.dirname(os.path.abspath(__file__))
        for fn in ("hindsight_mem.py", "test_hindsight_mem.py"):
            with open(os.path.join(here, fn), encoding="utf-8") as f:
                ast.parse(f.read(), filename=fn, feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
