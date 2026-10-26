#!/usr/bin/env python3
"""catalog.py 테스트 (unittest, 표준 라이브러리만).

임시 디렉터리에 가짜 작업공간을 만든다.

    <tmp>/                      ← 작업공간 루트
    ├── workspace-index/        ← 가짜 저장소 (scripts/catalog.py 복사본, catalog/, excluded.md)
    ├── alpha/                  ← git init + 커밋 1개
    ├── beta/                   ← 중첩 폴더 (mtime 규칙)
    ├── gamma/                  ← y 카드의 covers
    ├── work/x/, personal/y/    ← 깊이 2
    ├── secret/                 ← 절대 열거하지 않음
    └── 한/ (NFD 이름)           ← 카드 path는 NFC

맥별 위치표 테스트는 WORKSPACE_INDEX_MACHINE 으로 맥 이름을 정하고 machines/<맥>.md 를 쓴다.
환경변수가 없으면 맥 이름은 임시 폴더 이름이라 위치표가 없고, 기본 맥 규칙(카드 path)으로 돈다.

스크립트는 복사본을 subprocess로 실행한다(루트를 파일 위치로 찾는지 확인).
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT_SRC = os.path.join(HERE, "catalog.py")

TODAY = datetime.date.today().isoformat()

GROUPS = ["업무", "사이드 프로젝트", "생활", "학습·실험"]   # catalog.py 기본값

BODY = (
    "\n## 한눈에\n설명\n"
    "## 들어갈 때 읽을 것\n- README.md\n"
    "## 주의\n특이사항 없음\n"
    "## 관련 폴더\n없음\n"
)


def make_card(name, path, group="업무", status="active", covers="[]",
              sensitive="none", read_scope="[]", entry_docs="[README.md]",
              related="[]", aliases="[]", last_activity="2000-01-01",
              last_verified=TODAY, body=BODY):
    return (
        "---\n"
        "name: {name}\n"
        "path: {path}\n"
        "group: {group}\n"
        "status: {status}\n"
        "summary: {name} 요약\n"
        "stack: [문서]\n"
        "entry_docs: {entry_docs}\n"
        "covers: {covers}\n"
        "related: {related}\n"
        "aliases: {aliases}\n"
        "sensitive: {sensitive}\n"
        "read_scope: {read_scope}\n"
        "last_activity: {last_activity}\n"
        "last_verified: {last_verified}\n"
        "---\n"
    ).format(**locals()) + body


def touch(path, text="x", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class CatalogTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = os.path.realpath(self.tmp.name)
        self.repo = os.path.join(self.ws, "workspace-index")
        self.catalog = os.path.join(self.repo, "catalog")
        os.makedirs(os.path.join(self.repo, "scripts"))
        os.makedirs(self.catalog)
        self.script = os.path.join(self.repo, "scripts", "catalog.py")
        shutil.copy(SCRIPT_SRC, self.script)
        with open(os.path.join(self.repo, "excluded.md"), "w", encoding="utf-8") as f:
            f.write("# 카드 없는 폴더\n\n| 폴더 | 사유 |\n|---|---|\n"
                    "| work | 묶음 폴더 |\n| personal | 묶음 폴더 |\n")

        # alpha: git 저장소 + 커밋 1개 (커밋 날짜 = 오늘)
        alpha = os.path.join(self.ws, "alpha")
        touch(os.path.join(alpha, "README.md"))
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for cmd in (["git", "init", "-q"], ["git", "add", "."],
                    ["git", "commit", "-q", "-m", "init"]):
            subprocess.run(cmd, cwd=alpha, env=env, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # 작업트리의 미커밋 파일은 git 날짜에 영향 없어야 함
        touch(os.path.join(alpha, "later.txt"))

        # beta: git 없음, 중첩 파일. 가장 최근 mtime = 2024-03-05
        old = datetime.datetime(2023, 1, 1).timestamp()
        newer = datetime.datetime(2024, 3, 5, 12).timestamp()
        touch(os.path.join(self.ws, "beta", "README.md"), mtime=old)
        touch(os.path.join(self.ws, "beta", "sub", "deep", "f.txt"), mtime=newer)
        # 깊이 4는 무시되어야 함
        touch(os.path.join(self.ws, "beta", "a", "b", "c", "d.txt"),
              mtime=datetime.datetime(2025, 6, 6).timestamp())
        # node_modules 는 제외
        touch(os.path.join(self.ws, "beta", "node_modules", "m.js"),
              mtime=datetime.datetime(2025, 7, 7).timestamp())
        os.utime(os.path.join(self.ws, "beta"), (old, old))
        for d in ("sub", os.path.join("sub", "deep"), "a", os.path.join("a", "b"),
                  os.path.join("a", "b", "c"), "node_modules"):
            os.utime(os.path.join(self.ws, "beta", d), (old, old))

        touch(os.path.join(self.ws, "gamma", "README.md"))
        touch(os.path.join(self.ws, "work", "x", "README.md"))
        touch(os.path.join(self.ws, "personal", "y", "README.md"))
        touch(os.path.join(self.ws, "secret", "key.txt"))
        self.han_nfd = unicodedata.normalize("NFD", "한")
        self.han_nfc = unicodedata.normalize("NFC", "한")
        touch(os.path.join(self.ws, self.han_nfd, "README.md"))

        self.write_card("alpha", make_card("alpha", "alpha"))
        self.write_card("beta", make_card("beta", "beta", group="사이드 프로젝트",
                                          status="paused"))
        self.write_card("x", make_card("x", "work/x", group="생활",
                                       related="[alpha]", aliases="[엑스, x]"))
        self.write_card("y", make_card("y", "personal/y", group="학습·실험",
                                       covers="[gamma]", status="done"))
        self.write_card("han", make_card("han", self.han_nfc, group="업무"))

    def tearDown(self):
        self.tmp.cleanup()

    # --- 도우미 ---
    def write_card(self, name, text):
        with open(os.path.join(self.catalog, name + ".md"), "w", encoding="utf-8") as f:
            f.write(text)

    def read(self, rel):
        with open(os.path.join(self.repo, rel), "rb") as f:
            return f.read()

    def run_script(self, *args, machine=None):
        env = {k: v for k, v in os.environ.items() if k != "WORKSPACE_INDEX_MACHINE"}
        if machine:
            env["WORKSPACE_INDEX_MACHINE"] = machine
        p = subprocess.run([sys.executable, "-I", self.script] + list(args),
                           cwd=self.ws, stdout=subprocess.PIPE, env=env,
                           stderr=subprocess.STDOUT, timeout=60)
        return p.returncode, p.stdout.decode("utf-8", "replace")

    def build_and_check(self):
        code, out = self.run_script("build")
        self.assertEqual(code, 0, out)
        return self.run_script("check")

    # --- (a) 깨끗한 입력 ---
    def test_clean_workspace_passes(self):
        code, out = self.build_and_check()
        self.assertEqual(code, 0, out)
        self.assertNotIn("ERROR", out)
        self.assertTrue(out.rstrip().endswith("카드 5장, 오류 0, 경고 0"), out)

    def test_last_activity_git_and_mtime(self):
        self.run_script("build")
        alpha = self.read("catalog/alpha.md").decode("utf-8")
        self.assertIn("last_activity: " + TODAY + "\n", alpha)
        beta = self.read("catalog/beta.md").decode("utf-8")
        self.assertIn("last_activity: 2024-03-05\n", beta)

    # --- (b) 미등록 폴더 ---
    def test_unregistered_folder(self):
        self.run_script("build")
        touch(os.path.join(self.ws, "delta", "f.txt"))
        code, out = self.run_script("check")
        self.assertEqual(code, 1)
        self.assertIn("ERROR delta: 미등록 폴더", out)

    def test_folder_in_two_places(self):
        # gamma 가 y.covers 와 excluded.md 둘 다에 있음
        with open(os.path.join(self.repo, "excluded.md"), "a", encoding="utf-8") as f:
            f.write("| gamma | 중복 |\n")
        self.run_script("build")
        code, out = self.run_script("check")
        self.assertEqual(code, 1)
        self.assertIn("ERROR gamma:", out)

    # --- (c) 문법 위반 ---
    def test_trailing_comment_is_error(self):
        self.write_card("alpha", make_card("alpha", "alpha", status="active # 주석"))
        code, out = self.run_script("check", "--no-coverage")
        self.assertEqual(code, 1)
        self.assertIn("ERROR alpha:", out)

    def test_nested_value_is_error(self):
        self.write_card("alpha", make_card("alpha", "alpha", entry_docs="\n  - README.md"))
        code, out = self.run_script("check", "--no-coverage")
        self.assertEqual(code, 1)
        self.assertIn("ERROR alpha:", out)

    def test_unknown_and_missing_field(self):
        text = make_card("alpha", "alpha").replace("stack: [문서]\n", "stak: [문서]\n")
        self.write_card("alpha", text)
        code, out = self.run_script("check", "--no-coverage")
        self.assertEqual(code, 1)
        self.assertIn("stak", out)
        self.assertIn("stack", out)

    def test_bad_enum_and_name_mismatch(self):
        self.write_card("alpha", make_card("alpha", "alpha", status="running"))
        self.write_card("x", make_card("xx", "work/x"))
        code, out = self.run_script("check", "--no-coverage")
        self.assertEqual(code, 1)
        self.assertIn("ERROR alpha:", out)
        self.assertIn("ERROR x:", out)

    def test_body_headings_order(self):
        bad = BODY.replace("## 주의\n특이사항 없음\n", "")
        self.write_card("alpha", make_card("alpha", "alpha", body=bad))
        code, out = self.run_script("check", "--no-coverage")
        self.assertEqual(code, 1)
        self.assertIn("ERROR alpha:", out)

    # --- (d) pii + read_scope 빔 ---
    def test_pii_requires_read_scope(self):
        self.write_card("alpha", make_card("alpha", "alpha", sensitive="pii"))
        code, out = self.run_script("check", "--no-coverage")
        self.assertEqual(code, 1)
        self.assertIn("ERROR alpha:", out)
        self.assertIn("read_scope", out)
        # read_scope 를 채우면 통과
        touch(os.path.join(self.ws, "alpha", "docs", "a.md"))
        self.write_card("alpha", make_card("alpha", "alpha", sensitive="pii",
                                           read_scope="[docs]"))
        code, out = self.run_script("check", "--no-coverage")
        self.assertEqual(code, 0, out)

    def test_missing_entry_doc_and_related(self):
        self.write_card("alpha", make_card("alpha", "alpha", entry_docs="[nope.md]",
                                           related="[ghost]"))
        code, out = self.run_script("check", "--no-coverage")
        self.assertEqual(code, 1)
        self.assertIn("nope.md", out)
        self.assertIn("ghost", out)

    # --- (e) PROJECTS.md 불일치 ---
    def test_projects_mismatch_then_build(self):
        self.run_script("build")
        with open(os.path.join(self.repo, "PROJECTS.md"), "a", encoding="utf-8") as f:
            f.write("손으로 고침\n")
        code, out = self.run_script("check")
        self.assertEqual(code, 1)
        self.assertIn("ERROR PROJECTS.md:", out)
        code, out = self.build_and_check()
        self.assertEqual(code, 0, out)

    def test_projects_content(self):
        self.run_script("build")
        text = self.read("PROJECTS.md").decode("utf-8")
        self.assertTrue(text.startswith(
            "<!-- 자동 생성 파일. scripts/catalog.py build 로 다시 만든다. 직접 수정 금지. -->\n"
            "# 프로젝트 지도\n\n생성 " + TODAY + " · 카드 5장(removed 제외) · 활동 기준 last_activity\n"))
        for g in GROUPS:
            self.assertIn("\n## " + g + "\n\n", text)
        self.assertIn("| [alpha](catalog/alpha.md) | alpha 요약 | active | " + TODAY
                      + " | README.md |  | main |", text)
        self.assertIn("| [x](catalog/x.md) | x 요약 | active | ", text)
        self.assertIn("| 엑스, x |", text)
        self.assertIn("## 정본이 아닌 폴더\n\n| 폴더 | 정본 카드 | 비고 |\n|---|---|---|\n"
                      "| gamma | [y](catalog/y.md) | 카드 '관련 폴더' 절 참고 |\n", text)
        self.assertIn("## 카드 없는 폴더\n\n| 폴더 | 사유 |\n|---|---|\n"
                      "| work | 묶음 폴더 |\n| personal | 묶음 폴더 |\n", text)
        self.assertIn("## 폴더가 사라진 카드\n\n(없음)\n", text)
        self.assertIn("## 맥별 위치\n\n위 세 표와 카드 `path`는 기본 맥(main) 기준이다.", text)
        self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"))
        # 결정적: 두 번 build 해도 같은 바이트
        self.run_script("build")
        self.assertEqual(text, self.read("PROJECTS.md").decode("utf-8"))

    # --- (f) build 는 last_activity 줄만 바꾼다 ---
    def test_build_preserves_card_bytes(self):
        text = ("---\n# 머리 주석\n" + make_card("beta", "beta", group="사이드 프로젝트")[4:])
        text = text.replace("summary: beta 요약", "summary:   beta  요약   ")
        self.write_card("beta", text)
        before = self.read("catalog/beta.md")
        code, out = self.run_script("build")
        self.assertEqual(code, 0, out)
        after = self.read("catalog/beta.md")
        self.assertNotEqual(before, after)
        expect = before.replace(b"last_activity: 2000-01-01\n",
                                b"last_activity: 2024-03-05\n")
        self.assertEqual(after, expect)
        # 변경이 없으면 파일을 다시 쓰지 않는다 (mtime 유지)
        path = os.path.join(self.catalog, "beta.md")
        old = datetime.datetime(2020, 1, 1).timestamp()
        os.utime(path, (old, old))
        self.run_script("build")
        self.assertEqual(int(os.stat(path).st_mtime), int(old))

    def test_removed_card_keeps_value_and_is_listed(self):
        self.write_card("gone", make_card("gone", "gone", status="removed",
                                          last_activity="2021-02-03"))
        code, out = self.build_and_check()
        self.assertEqual(code, 0, out)
        self.assertIn("last_activity: 2021-02-03\n",
                      self.read("catalog/gone.md").decode("utf-8"))
        text = self.read("PROJECTS.md").decode("utf-8")
        self.assertIn("카드 5장(removed 제외)", text)
        self.assertNotIn("[gone](catalog/gone.md) | gone 요약", text.split("## 폴더가 사라진 카드")[0])
        self.assertIn("gone", text.split("## 폴더가 사라진 카드")[1])
        # 폴더가 다시 생기면 WARN
        touch(os.path.join(self.ws, "gone", "README.md"))
        code, out = self.run_script("check", "--no-coverage")
        self.assertEqual(code, 0, out)
        self.assertIn("WARN gone:", out)

    # --- (g) NFD 폴더 ↔ NFC path ---
    def test_nfd_folder_matches_nfc_path(self):
        code, out = self.build_and_check()
        self.assertEqual(code, 0, out)
        self.assertNotIn("미등록", out)
        self.assertNotIn(self.han_nfd, self.read("PROJECTS.md").decode("utf-8"))
        self.assertIn("| [han](catalog/han.md) |", self.read("PROJECTS.md").decode("utf-8"))

    # --- (h) secret 은 절대 나오지 않음 ---
    def test_secret_never_listed(self):
        for name in os.listdir(self.catalog):
            os.remove(os.path.join(self.catalog, name))
        self.run_script("build")
        code, out = self.run_script("check")
        self.assertEqual(code, 1)
        self.assertIn("ERROR alpha: 미등록 폴더", out)
        self.assertNotIn("secret", out)
        self.assertNotIn("secret", self.read("PROJECTS.md").decode("utf-8"))
        self.assertNotIn("workspace-index: 미등록", out)

    # --- (i) --no-coverage ---
    def test_no_coverage_skips(self):
        touch(os.path.join(self.ws, "delta", "f.txt"))
        # PROJECTS.md 없음 + 미등록 폴더 → 둘 다 건너뜀
        code, out = self.run_script("check", "--no-coverage")
        self.assertEqual(code, 0, out)
        self.assertNotIn("미등록", out)
        code, out = self.run_script("check")
        self.assertEqual(code, 1)

    # --- 경고 ---
    def test_warnings(self):
        self.run_script("build")
        self.write_card("x", make_card("x", "work/x", group="생활",
                                       aliases="[엑스]", last_verified="2020-01-01",
                                       last_activity="1999-01-01"))
        self.write_card("y", make_card("y", "personal/y", group="학습·실험",
                                       covers="[gamma]", aliases="[엑스]"))
        self.run_script("build")
        code, out = self.run_script("check")
        self.assertEqual(code, 0, out)
        self.assertIn("WARN x:", out)
        self.assertIn("WARN y:", out)
        self.assertRegex(out, r"WARN x: .*30일")
        self.assertRegex(out, r"WARN x: .*last_verified")
        self.assertRegex(out, r"WARN .*엑스")
        # build 전 값이 다르면 "build 필요"
        self.write_card("alpha", make_card("alpha", "alpha"))
        code, out = self.run_script("check")
        self.assertRegex(out, r"WARN alpha: .*build 필요")

    # --- 새 규칙: 요약 마침표·미래 날짜·대체 카드 후속·자격증명 주의 절 ---
    def check_nc(self):
        return self.run_script("check", "--no-coverage")

    def test_summary_trailing_period_is_error(self):
        text = make_card("alpha", "alpha").replace("summary: alpha 요약\n",
                                                   "summary: alpha 요약.\n")
        self.write_card("alpha", text)
        code, out = self.check_nc()
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"ERROR alpha: summary.*마침표")
        # 중간의 마침표는 괜찮다
        text = make_card("alpha", "alpha").replace("summary: alpha 요약\n",
                                                   "summary: v1.2 alpha 요약\n")
        self.write_card("alpha", text)
        code, out = self.check_nc()
        self.assertEqual(code, 0, out)

    def test_future_last_verified_is_error(self):
        self.write_card("alpha", make_card("alpha", "alpha", last_verified="9999-12-31"))
        code, out = self.check_nc()
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"ERROR alpha: last_verified 9999-12-31 .*미래")

    def test_future_last_activity_is_error(self):
        self.write_card("alpha", make_card("alpha", "alpha", last_activity="9999-12-31",
                                           last_verified="9999-12-31"))
        code, out = self.check_nc()
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"ERROR alpha: last_activity 9999-12-31 .*미래")

    def test_today_dates_pass(self):
        self.write_card("alpha", make_card("alpha", "alpha", last_activity=TODAY,
                                           last_verified=TODAY))
        code, out = self.check_nc()
        self.assertEqual(code, 0, out)
        self.assertNotIn("미래", out)

    def test_superseded_without_related_is_error(self):
        self.write_card("alpha", make_card("alpha", "alpha", status="superseded"))
        code, out = self.check_nc()
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"ERROR alpha: status superseded .*related")

    def test_superseded_first_related_missing_card(self):
        self.write_card("alpha", make_card("alpha", "alpha", status="superseded",
                                           related="[ghost, x]"))
        code, out = self.check_nc()
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"ERROR alpha: status superseded .*`ghost`.*카드가 없음")

    def test_superseded_first_related_removed(self):
        self.write_card("gone", make_card("gone", "gone", status="removed"))
        self.write_card("alpha", make_card("alpha", "alpha", status="superseded",
                                           related="[gone, x]"))
        code, out = self.check_nc()
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"ERROR alpha: status superseded .*`gone`.*removed")

    def test_superseded_first_related_superseded(self):
        self.write_card("beta", make_card("beta", "beta", group="사이드 프로젝트",
                                          status="superseded", related="[x]"))
        self.write_card("alpha", make_card("alpha", "alpha", status="superseded",
                                           related="[beta, x]"))
        code, out = self.check_nc()
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"ERROR alpha: status superseded .*`beta`.*superseded")
        self.assertNotRegex(out, r"ERROR beta: status superseded")

    def test_superseded_with_live_successor_passes(self):
        self.write_card("alpha", make_card("alpha", "alpha", status="superseded",
                                           related="[x, beta]"))
        code, out = self.check_nc()
        self.assertEqual(code, 0, out)

    def test_credentials_requires_caution_text(self):
        empty = BODY.replace("## 주의\n특이사항 없음\n", "## 주의\n  \n\t\n")
        self.write_card("alpha", make_card("alpha", "alpha", sensitive="credentials",
                                           body=empty))
        code, out = self.check_nc()
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"ERROR alpha: sensitive: credentials .*주의")
        # 주의 절에 내용이 있으면 통과
        self.write_card("alpha", make_card("alpha", "alpha", sensitive="credentials"))
        code, out = self.check_nc()
        self.assertEqual(code, 0, out)
        # credentials 가 아니면 빈 주의 절도 괜찮다
        self.write_card("alpha", make_card("alpha", "alpha", body=empty))
        code, out = self.check_nc()
        self.assertEqual(code, 0, out)

    def test_new_rules_do_not_block_build(self):
        empty = BODY.replace("## 주의\n특이사항 없음\n", "## 주의\n\n")
        text = make_card("alpha", "alpha", status="superseded", sensitive="credentials",
                         last_verified="9999-12-31", body=empty)
        text = text.replace("summary: alpha 요약\n", "summary: alpha 요약.\n")
        self.write_card("alpha", text)
        self.write_card("gone", make_card("gone", "gone", status="removed",
                                          last_activity="9999-12-31"))
        code, out = self.run_script("build")
        self.assertEqual(code, 0, out)
        self.assertNotIn("ERROR", out)
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "PROJECTS.md")))
        code, out = self.run_script("check")
        self.assertEqual(code, 1, out)
        for pat in (r"ERROR alpha: summary", r"ERROR alpha: last_verified",
                    r"ERROR alpha: status superseded", r"ERROR alpha: sensitive: credentials",
                    r"ERROR gone: last_activity"):
            self.assertRegex(out, pat)
        self.assertNotIn("ERROR PROJECTS.md", out)


    # --- 빈 catalog ---
    def test_empty_catalog_build(self):
        for name in os.listdir(self.catalog):
            os.remove(os.path.join(self.catalog, name))
        code, out = self.run_script("build")
        self.assertEqual(code, 0, out)
        text = self.read("PROJECTS.md").decode("utf-8")
        self.assertIn("카드 0장(removed 제외)", text)
        for g in GROUPS:
            self.assertIn("\n## " + g + "\n\n(없음)\n\n", text)
        # 11개 그룹 + 정본이 아닌 폴더 + 폴더가 사라진 카드 + 맥별 위치 (카드 없는 폴더는 excluded 표가 있음)
        self.assertEqual(text.count("(없음)"), len(GROUPS) + 3)

    # --- 맥별 위치표 ---
    def write_machine(self, name, paths, covers=(), excluded=()):
        lines = ["# 맥 " + name, "", "| 카드 | 경로 |", "|---|---|"]
        lines += ["| %s | %s |" % row for row in paths]
        if covers:
            lines += ["", "| 폴더 | 정본 카드 |", "|---|---|"]
            lines += ["| %s | %s |" % row for row in covers]
        if excluded:
            lines += ["", "| 폴더 | 사유 |", "|---|---|"]
            lines += ["| %s | %s |" % row for row in excluded]
        os.makedirs(os.path.join(self.repo, "machines"), exist_ok=True)
        with open(os.path.join(self.repo, "machines", name + ".md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def setup_other(self):
        """맥 other: alpha 는 같은 이름, y 는 delta 폴더, x·beta 는 없다."""
        touch(os.path.join(self.ws, "delta", "README.md"))
        self.write_machine(
            "other",
            paths=[("alpha", "alpha"), ("y", "delta"), ("han", self.han_nfc)],
            covers=[("gamma", "y")],
            excluded=[("work", "묶음"), ("personal", "묶음"), ("beta", "다른 용도"),
                      ("work/x", "다른 용도"), ("personal/y", "다른 용도")])

    def test_machine_layout_passes(self):
        self.setup_other()
        code, out = self.run_script("build", machine="other")
        self.assertEqual(code, 0, out)
        self.assertIn("(맥 other)", out)
        code, out = self.run_script("check", machine="other")
        self.assertEqual(code, 0, out)
        self.assertNotIn("ERROR", out)
        text = self.read("PROJECTS.md").decode("utf-8")
        self.assertIn("| README.md |  | main, other |", text)
        self.assertIn("| other | [machines/other.md](machines/other.md) | 3 | 1 | 5 |", text)
        # 기본 맥에서는 delta 가 미등록 폴더
        code, out = self.run_script("check")
        self.assertEqual(code, 1)
        self.assertIn("ERROR delta: 미등록 폴더", out)

    def test_machine_backtick_cell_keeps_trailing_space(self):
        self.setup_other()
        touch(os.path.join(self.ws, "delta ", "f.txt"))
        self.run_script("build", machine="other")
        code, out = self.run_script("check", machine="other")
        self.assertIn("ERROR delta : 미등록 폴더", out)
        with open(os.path.join(self.repo, "machines", "other.md"), "a", encoding="utf-8") as f:
            f.write("| `delta ` | 끝에 공백이 붙은 빈 폴더 |\n")
        self.run_script("build", machine="other")
        code, out = self.run_script("check", machine="other")
        self.assertEqual(code, 0, out)

    def test_machine_unregistered_and_missing(self):
        self.setup_other()
        self.run_script("build", machine="other")
        touch(os.path.join(self.ws, "epsilon", "f.txt"))
        shutil.rmtree(os.path.join(self.ws, "gamma"))
        code, out = self.run_script("check", machine="other")
        self.assertEqual(code, 1)
        self.assertIn("ERROR epsilon: 미등록 폴더", out)
        self.assertIn("ERROR y: covers `gamma` 가 존재하지 않음", out)

    def test_machine_entry_doc_missing_is_warning(self):
        self.setup_other()
        os.remove(os.path.join(self.ws, "delta", "README.md"))
        touch(os.path.join(self.ws, "delta", "other.txt"))
        self.run_script("build", machine="other")
        code, out = self.run_script("check", machine="other")
        self.assertEqual(code, 0, out)
        self.assertIn("WARN y: entry_docs `README.md`", out)

    def test_machine_file_errors(self):
        self.write_machine("other", paths=[("ghost", "alpha"), ("alpha", "alpha")],
                           covers=[("gamma", "nobody")])
        self.write_machine("main", paths=[("alpha", "alpha")])
        code, out = self.run_script("check", "--no-coverage")
        self.assertEqual(code, 1)
        self.assertIn("ERROR machines/other.md: 카드 `ghost` 가 catalog 에 없음", out)
        self.assertIn("ERROR machines/other.md: `alpha` 가 둘 이상에 적힘", out)
        self.assertIn("정본 카드 `nobody`", out)
        self.assertIn("ERROR machines/main.md: 기본 맥(main)", out)
        # 구조 오류가 있으면 build 는 아무것도 쓰지 않는다
        code, out = self.run_script("build")
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "PROJECTS.md")))

    # --- config.json ---
    def write_config(self, data):
        with open(os.path.join(self.repo, "config.json"), "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))

    def test_config_custom_groups_bundles_and_machine(self):
        self.write_config({"groups": ["고객사", "개인"], "bundle_dirs": ["clients"],
                           "default_machine": "laptop"})
        for name in ("alpha", "beta", "x", "y", "han"):
            os.remove(os.path.join(self.catalog, name + ".md"))
        for d in ("alpha", "beta", "gamma", "work", "personal", self.han_nfd):
            shutil.rmtree(os.path.join(self.ws, d))
        with open(os.path.join(self.repo, "excluded.md"), "w", encoding="utf-8") as f:
            f.write("# 카드 없는 폴더\n\n| 폴더 | 사유 |\n|---|---|\n| clients | 묶음 폴더 |\n")
        touch(os.path.join(self.ws, "clients", "acme", "README.md"))
        self.write_card("acme", make_card("acme", "clients/acme", group="고객사"))
        code, out = self.build_and_check()
        self.assertEqual(code, 0, out)
        self.assertIn("## 고객사", self.read("PROJECTS.md").decode("utf-8"))
        # 설정에 없는 그룹은 ERROR
        self.write_card("acme", make_card("acme", "clients/acme", group="업무"))
        code, out = self.run_script("check", "--no-coverage")
        self.assertEqual(code, 1)
        self.assertIn("group", out)
        # default_machine 이름의 위치표는 구조 오류
        self.write_card("acme", make_card("acme", "clients/acme", group="고객사"))
        self.write_machine("laptop", paths=[("acme", "clients/acme")])
        code, out = self.run_script("check", "--no-coverage")
        self.assertIn("ERROR machines/laptop.md: 기본 맥(laptop)", out)

    def test_config_errors_stop_everything(self):
        self.write_config("{not json")
        code, out = self.run_script("build")
        self.assertEqual(code, 1)
        self.assertIn("ERROR config.json: JSON 으로 읽을 수 없음", out)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "PROJECTS.md")))
        self.write_config({"groups": [], "bundle_dirs": ["a/b"], "default_machine": "Main",
                           "extra": 1})
        code, out = self.run_script("check")
        self.assertEqual(code, 1)
        for part in ("모르는 키: extra", "groups", "bundle_dirs", "default_machine"):
            self.assertIn(part, out)

    def test_path_dash_card(self):
        touch(os.path.join(self.ws, "zeta", "README.md"))
        self.write_card("z", make_card("z", "-", group="사이드 프로젝트"))
        code, out = self.run_script("check", "--no-coverage")
        self.assertEqual(code, 1)
        self.assertIn("ERROR z: path 가 `-` 인데 어느 맥 위치표에도 경로가 없음", out)
        self.write_machine("other", paths=[("z", "zeta")])
        with open(os.path.join(self.repo, "excluded.md"), "a", encoding="utf-8") as f:
            f.write("| zeta | 다른 맥 전용 프로젝트의 사본 |\n")
        code, out = self.build_and_check()
        self.assertEqual(code, 0, out)
        self.assertIn("| [z](catalog/z.md) | z 요약 | active | 2000-01-01 | README.md |  | other |",
                      self.read("PROJECTS.md").decode("utf-8"))

    def test_last_activity_keeps_latest(self):
        # beta 의 이 맥 계산값은 2024-03-05. 저장값이 더 늦으면(다른 맥에서 본 날짜) 그대로 둔다.
        # 미래 날짜는 check 의 카드 내용 규칙 ERROR 라 오늘 이전 날짜로 본다.
        self.write_card("beta", make_card("beta", "beta", group="사이드 프로젝트", status="paused",
                                          last_activity="2025-01-01"))
        code, out = self.build_and_check()
        self.assertEqual(code, 0, out)
        self.assertIn("last_activity: 2025-01-01\n", self.read("catalog/beta.md").decode("utf-8"))
        self.assertNotIn("WARN beta", out)

    def test_projects_same_on_every_machine(self):
        self.setup_other()
        self.run_script("build")
        first = self.read("PROJECTS.md")
        self.run_script("build", machine="other")
        self.assertEqual(first, self.read("PROJECTS.md"))



# ---------------------------------------------------------------------------
# resolve: 키워드 → 후보 카드 (ORCHESTRATOR.md 3절)
# ---------------------------------------------------------------------------

class ResolveTestCase(unittest.TestCase):
    """resolve 는 카드·excluded.md·위치표만 읽는다. 작업공간 폴더는 만들지 않는다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = os.path.realpath(self.tmp.name)
        self.repo = os.path.join(self.ws, "workspace-index")
        self.catalog = os.path.join(self.repo, "catalog")
        os.makedirs(os.path.join(self.repo, "scripts"))
        os.makedirs(self.catalog)
        self.script = os.path.join(self.repo, "scripts", "catalog.py")
        shutil.copy(SCRIPT_SRC, self.script)
        with open(os.path.join(self.repo, "excluded.md"), "w", encoding="utf-8") as f:
            f.write("# 카드 없는 폴더\n\n| 폴더 | 사유 |\n|---|---|\n"
                    "| work | 묶음 폴더 |\n| personal | 묶음 폴더 |\n"
                    "| testbed | 잡동사니 |\n")
        cards = {
            "cafe-booking": make_card("cafe-booking", "cafe-booking", aliases="[카페, 좌석 예약]"),
            "cafe-menu": make_card("cafe-menu", "cafe-menu", status="done",
                                   aliases="[카페 메뉴판, 동네 카페 메뉴]"),
            "toolbox": make_card("toolbox", "work/lunch-stats", status="template",
                                 covers="[form-helper]",
                                 aliases="[도구 상자, 집안일 도구 상자]"),
            "chore-bot": make_card("chore-bot", "집안일자동화", aliases="[도구 상자, 알림 자동화]"),
            "old-diary": make_card("old-diary", "diary-v1", status="superseded",
                                   related="[journal-app]",
                                   aliases="[일기장 v1, 하루 기록]"),
            "journal-app": make_card("journal-app", "journal_app", aliases="[일기, 감정 기록]"),
            "plant-care": make_card("plant-care", "work/PlantCare", aliases="[화분, 물주기]"),
            "home-wiki": make_card("home-wiki", "Home wiki", group="생활", aliases="[위키]"),
            "recipe-box": make_card("recipe-box", "recipe_box", aliases="[레시피]"),
            "gone": make_card("gone", "gone-folder", status="removed", aliases="[사라진 도구]"),
            "book-club": make_card("book-club", "book-club", status="done",
                                   covers="[my_blog]", aliases="[독서 모임]"),
            "my-blog": make_card("my-blog", "personal/my-blog", aliases="[블로그]"),
        }
        for name, text in cards.items():
            self.write_card(name, text)

    def tearDown(self):
        self.tmp.cleanup()

    def write_card(self, name, text):
        with open(os.path.join(self.catalog, name + ".md"), "w", encoding="utf-8") as f:
            f.write(text)

    def resolve(self, *keywords, machine=None):
        env = {k: v for k, v in os.environ.items() if k != "WORKSPACE_INDEX_MACHINE"}
        if machine:
            env["WORKSPACE_INDEX_MACHINE"] = machine
        p = subprocess.run([sys.executable, "-I", self.script, "resolve"] + list(keywords),
                           cwd=self.ws, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=env, timeout=60)
        return (p.returncode, p.stdout.decode("utf-8", "replace"),
                p.stderr.decode("utf-8", "replace"))

    def last_line(self, out):
        return out.rstrip("\n").split("\n")[-1]

    def line_of(self, out, prefix, name):
        """`prefix name` 으로 시작하는(앞 공백 무시) 줄."""
        for line in out.split("\n"):
            if line.strip().startswith("%s %s " % (prefix, name)) or line.strip() == "%s %s" % (prefix, name):
                return line
        return None

    # --- 기본 판정 ---
    def test_exact_single(self):
        code, out, _ = self.resolve("좌석 예약")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.last_line(out), "후보 1개: cafe-booking")
        line = self.line_of(out, "후보", "cafe-booking")
        self.assertIsNotNone(line, out)
        for part in ("cafe-booking", "active", "별칭", "좌석 예약", "완전 일치"):
            self.assertIn(part, line)

    def test_exact_and_partial_partial_is_reference(self):
        code, out, _ = self.resolve("카페")
        self.assertEqual(code, 0)
        self.assertEqual(self.last_line(out), "후보 1개: cafe-booking")
        ref = self.line_of(out, "참고", "cafe-menu")
        self.assertIsNotNone(ref, out)
        self.assertIn("부분 일치", ref)
        self.assertIsNone(self.line_of(out, "후보", "cafe-menu"))

    def test_partial_only_becomes_candidate(self):
        code, out, _ = self.resolve("동네")
        self.assertEqual(self.last_line(out), "후보 1개: cafe-menu")
        self.assertIn("부분 일치", self.line_of(out, "후보", "cafe-menu"))

    def test_partial_keyword_contains_label(self):
        # 이름표 ⊂ 키워드
        code, out, _ = self.resolve("물주기알림설정")
        self.assertEqual(self.last_line(out), "후보 1개: plant-care")

    def test_no_match(self):
        code, out, _ = self.resolve("없는말")
        self.assertEqual(code, 0)
        self.assertIn("일치 없음", out)
        self.assertEqual(self.last_line(out), "후보 0개")

    def test_partial_needs_two_chars(self):
        # 정규화 뒤 1자 키워드는 부분 일치를 만들지 않는다
        code, out, _ = self.resolve("위")
        self.assertEqual(self.last_line(out), "후보 0개")

    # --- 정규화 ---
    def test_normalization_space_hyphen_underscore_case(self):
        for kw in ("home wiki", "HOME wiki", "home-wiki", "HOME_WIKI"):
            code, out, _ = self.resolve(kw)
            self.assertEqual(self.last_line(out), "후보 1개: home-wiki", kw + "\n" + out)
            self.assertIn("완전 일치", self.line_of(out, "후보", "home-wiki"))
        for kw in ("recipe box", "recipe_box", "Recipe-Box"):
            code, out, _ = self.resolve(kw)
            self.assertEqual(self.last_line(out), "후보 1개: recipe-box", kw)

    def test_nfd_keyword(self):
        code, out, _ = self.resolve(unicodedata.normalize("NFD", "카페"))
        self.assertEqual(self.last_line(out), "후보 1개: cafe-booking")

    def test_empty_after_normalization(self):
        code, out, _ = self.resolve("-")
        self.assertEqual(code, 0)
        self.assertIn("일치 없음", out)
        self.assertEqual(self.last_line(out), "후보 0개")

    # --- 폴더 이름표 ---
    def test_real_folder_name(self):
        code, out, _ = self.resolve("PlantCare")
        self.assertEqual(self.last_line(out), "후보 1개: plant-care")
        code, out, _ = self.resolve("lunch-stats")
        self.assertEqual(self.last_line(out), "후보 1개: toolbox")
        self.assertIn("폴더", self.line_of(out, "후보", "toolbox"))

    def test_copy_folder_points_to_canonical(self):
        code, out, _ = self.resolve("form_helper")
        self.assertEqual(self.last_line(out), "후보 1개: toolbox")
        self.assertIn("사본 폴더 `form-helper`의 정본", self.line_of(out, "후보", "toolbox"))

    def test_copy_folder_and_label_same_canonical_once(self):
        # 정규화하면 사본 폴더 my_blog 와 카드 my-blog 이름이 같다 → 정본이 다르므로 둘 다 후보
        code, out, _ = self.resolve("my_blog")
        self.assertEqual(self.last_line(out), "후보 2개: book-club, my-blog")
        self.assertIn("사본 폴더", self.line_of(out, "후보", "book-club"))
        # 같은 정본: toolbox 의 별칭과 사본 폴더가 같은 키워드에 동시에 완전 일치
        self.write_card("toolbox", make_card(
            "toolbox", "work/lunch-stats", status="template",
            covers="[form-helper]", aliases="[도구 상자, form helper]"))
        code, out, _ = self.resolve("form_helper")
        self.assertEqual(self.last_line(out), "후보 1개: toolbox")
        self.assertEqual(out.count("후보 toolbox"), 1, out)

    # --- 상태 ---
    def test_superseded_redirects_to_successor(self):
        code, out, _ = self.resolve("일기장 v1")
        self.assertEqual(self.last_line(out), "후보 1개: journal-app")
        line = self.line_of(out, "후보", "journal-app")
        self.assertIn("옛 이름 일기장 v1로 찾음 — old-diary는 대체됨(읽기 전용)", line)
        self.assertIsNotNone(self.line_of(out, "참고", "old-diary"), out)

    def test_superseded_successor_already_candidate(self):
        # 후속 카드가 같은 키워드로 이미 후보 → 한 번만, 표시는 둘 다
        self.write_card("journal-app", make_card("journal-app", "journal_app",
                                                 aliases="[일기, 하루 기록]"))
        code, out, _ = self.resolve("하루 기록")
        self.assertEqual(self.last_line(out), "후보 1개: journal-app")
        self.assertEqual(out.count("후보 journal-app"), 1, out)
        line = self.line_of(out, "후보", "journal-app")
        self.assertIn("완전 일치", line)
        self.assertIn("옛 이름 하루 기록로 찾음", line)

    def test_superseded_partial_only_with_other_exact(self):
        # old-diary 의 별칭을 부분으로만 품는 완전 일치를 다른 카드에 만든다
        self.write_card("journal-app", make_card("journal-app", "journal_app",
                                                 aliases="[일기, 일기장]"))
        code, out, _ = self.resolve("일기장")
        self.assertEqual(self.last_line(out), "후보 1개: journal-app")
        self.assertIn("참고 old-diary", out)
        self.assertNotIn("옛 이름", out)

    def test_removed_is_reference(self):
        code, out, _ = self.resolve("사라진 도구")
        self.assertEqual(self.last_line(out), "후보 0개")
        line = self.line_of(out, "참고(폴더 없음)", "gone")
        self.assertIsNotNone(line, out)

    # --- excluded.md ---
    def test_excluded_exact_only(self):
        code, out, _ = self.resolve("testbed")
        self.assertIn("카드 없는 폴더 testbed(사유: 잡동사니)", out)
        self.assertEqual(self.last_line(out), "후보 0개")
        code, out, _ = self.resolve("testbe")
        self.assertNotIn("카드 없는 폴더", out)
        self.assertEqual(self.last_line(out), "후보 0개")

    # --- 여러 키워드 ---
    def test_multiple_keywords_union(self):
        code, out, _ = self.resolve("카페", "home wiki", "없는말")
        self.assertEqual(code, 0)
        self.assertEqual(self.last_line(out), "후보 2개: cafe-booking, home-wiki")
        self.assertIn("일치 없음", out)

    def test_multiple_keywords_same_card_once(self):
        code, out, _ = self.resolve("카페", "좌석 예약")
        self.assertEqual(self.last_line(out), "후보 1개: cafe-booking")

    def test_alias_overlap_two_candidates(self):
        code, out, _ = self.resolve("도구 상자")
        self.assertEqual(self.last_line(out), "후보 2개: chore-bot, toolbox")

    def test_same_card_many_labels_strongest(self):
        # toolbox: 별칭 `도구 상자` 완전 + `집안일 도구 상자` 부분 → 한 번, 완전 일치
        code, out, _ = self.resolve("도구 상자")
        self.assertEqual(out.count("toolbox"), 2, out)  # 후보 줄 + 마지막 줄
        self.assertIn("완전 일치", self.line_of(out, "후보", "toolbox"))
        self.assertNotIn("부분 일치", self.line_of(out, "후보", "toolbox"))

    # --- 사용법·오류 ---
    def test_no_args_exit_2(self):
        code, out, err = self.resolve()
        self.assertEqual(code, 2)

    def test_broken_card_skipped_with_stderr(self):
        self.write_card("broken", "---\nname: broken\n---\n")
        code, out, err = self.resolve("broken")
        self.assertEqual(code, 0)
        self.assertEqual(self.last_line(out), "후보 0개")
        self.assertEqual(len([l for l in err.split("\n") if "broken" in l]), 1, err)

    def test_resolve_writes_nothing(self):
        before = {fn: os.path.getmtime(os.path.join(self.catalog, fn)) for fn in os.listdir(self.catalog)}
        self.resolve("카페")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "PROJECTS.md")))
        after = {fn: os.path.getmtime(os.path.join(self.catalog, fn)) for fn in os.listdir(self.catalog)}
        self.assertEqual(before, after)

    def test_other_machine_uses_location_table(self):
        os.makedirs(os.path.join(self.repo, "machines"))
        with open(os.path.join(self.repo, "machines", "other.md"), "w", encoding="utf-8") as f:
            f.write("# 맥 other\n\n| 카드 | 경로 |\n|---|---|\n| cafe-booking | work/cafeSeats2 |\n"
                    "\n| 폴더 | 정본 카드 |\n|---|---|\n| cafe-copy | cafe-booking |\n"
                    "\n| 폴더 | 사유 |\n|---|---|\n| scratchpad | 잡동사니 |\n")
        code, out, _ = self.resolve("cafeseats2", machine="other")
        self.assertEqual(self.last_line(out), "후보 1개: cafe-booking")
        line = self.line_of(out, "후보", "cafe-booking")
        self.assertIn("path work/cafeSeats2", line)
        self.assertIn("폴더 `cafeSeats2` 완전 일치", line)
        code, out, _ = self.resolve("cafe-copy", machine="other")
        self.assertIn("사본 폴더 `cafe-copy`의 정본", self.line_of(out, "후보", "cafe-booking"))
        # 카드 없는 폴더는 이 맥의 위치표 기준
        code, out, _ = self.resolve("scratchpad", "testbed", machine="other")
        self.assertIn("카드 없는 폴더 scratchpad", out)
        self.assertNotIn("카드 없는 폴더 testbed", out)
        # 이 맥에 폴더가 없는 카드는 그렇게 표시한다
        code, out, _ = self.resolve("블로그", machine="other")
        self.assertIn("path -(이 맥 other 에 없음)", self.line_of(out, "후보", "my-blog"))
        # 기본 맥은 그대로 카드 path
        code, out, _ = self.resolve("블로그")
        self.assertIn("path personal/my-blog", self.line_of(out, "후보", "my-blog"))

    def test_run_resolve_importable(self):
        sys.path.insert(0, os.path.dirname(self.script))
        try:
            import importlib
            mod = importlib.import_module("catalog")
            importlib.reload(mod)
            import io
            buf, ebuf = io.StringIO(), io.StringIO()
            code = mod.run_resolve(self.repo, self.ws, ["카페"], out=buf, err=ebuf)
        finally:
            sys.path.pop(0)
        self.assertEqual(code, 0)
        self.assertEqual(self.last_line(buf.getvalue()), "후보 1개: cafe-booking")



MARK = "MARKER_FILE_CONTENT_7731"
CAUTION_BODY = (
    "\n## 한눈에\n설명\n"
    "## 들어갈 때 읽을 것\n- README.md\n"
    "## 주의\n- `config.json` 에 자격증명이 있다. 열지 않는다.\n- 둘째 줄 주의 ZZ_CAUTION_END\n"
    "## 관련 폴더\n없음\n"
)
BRIEF_LAST = "작업 범위: (오케스트레이터가 채움 — 할 것 / 하지 않을 것)"
READ_ONLY_LINE = "이 폴더는 읽기 전용이다. 파일을 만들거나 고치거나 지우지 않는다"


class BriefTestCase(unittest.TestCase):
    """brief 는 카드를 읽고, 작업공간 쪽은 이름·존재 여부만 본다. 아무것도 쓰지 않는다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = os.path.realpath(self.tmp.name)
        self.repo = os.path.join(self.ws, "workspace-index")
        self.catalog = os.path.join(self.repo, "catalog")
        os.makedirs(os.path.join(self.repo, "scripts"))
        os.makedirs(self.catalog)
        self.script = os.path.join(self.repo, "scripts", "catalog.py")
        shutil.copy(SCRIPT_SRC, self.script)
        with open(os.path.join(self.repo, "excluded.md"), "w", encoding="utf-8") as f:
            f.write("# 카드 없는 폴더\n\n| 폴더 | 사유 |\n|---|---|\n")

        # both: CLAUDE.md + AGENTS.md (표식 문자열), entry_docs 3개
        both = os.path.join(self.ws, "both")
        touch(os.path.join(both, "CLAUDE.md"), MARK)
        touch(os.path.join(both, "AGENTS.md"), MARK)
        touch(os.path.join(both, "README.md"), MARK)
        touch(os.path.join(both, "docs", "b.md"), MARK)
        touch(os.path.join(both, "secret", "token.txt"), MARK)
        # cred: AGENTS.md 만, entry_docs 하나는 사라짐
        cred = os.path.join(self.ws, "cred")
        touch(os.path.join(cred, "AGENTS.md"), MARK)
        touch(os.path.join(cred, "README.md"), MARK)
        touch(os.path.join(cred, "config.json"), MARK)
        # onlyclaude: CLAUDE.md 만
        touch(os.path.join(self.ws, "onlyclaude", "CLAUDE.md"), MARK)
        touch(os.path.join(self.ws, "onlyclaude", "README.md"), MARK)
        # work/<NFD 한글>: pii, 규칙 파일 없음, read_scope 는 NFD 하위폴더 포함
        self.han_nfd = unicodedata.normalize("NFD", "고객")
        self.sub_nfd = unicodedata.normalize("NFD", "코드")
        pii = os.path.join(self.ws, "work", self.han_nfd)
        touch(os.path.join(pii, "README.md"), MARK)
        touch(os.path.join(pii, self.sub_nfd, "x.md"), MARK)
        touch(os.path.join(pii, "customers.csv"), MARK)
        self.pii_real = pii
        touch(os.path.join(self.ws, "old-folder", "README.md"), MARK)
        touch(os.path.join(self.ws, "secret", "key.txt"), MARK)

        cards = {
            "both": make_card("both", "both", entry_docs="[README.md, docs/b.md, CLAUDE.md]"),
            "cred": make_card("cred", "cred", sensitive="credentials",
                              entry_docs="[README.md, gone.md, AGENTS.md]", body=CAUTION_BODY),
            "onlyclaude": make_card("onlyclaude", "onlyclaude"),
            "pii": make_card("pii", "work/고객", group="업무", sensitive="pii",
                             read_scope="[코드, ghost]"),
            "old": make_card("old", "old-folder", status="superseded", related="[both, cred]"),
            "gone": make_card("gone", "gone-folder", status="removed"),
            "nopath": make_card("nopath", "missing-folder"),
            "done": make_card("done", "onlyclaude", status="done"),
        }
        for name, text in cards.items():
            with open(os.path.join(self.catalog, name + ".md"), "w", encoding="utf-8") as f:
                f.write(text)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, machine=None):
        env = {k: v for k, v in os.environ.items() if k != "WORKSPACE_INDEX_MACHINE"}
        if machine:
            env["WORKSPACE_INDEX_MACHINE"] = machine
        p = subprocess.run([sys.executable, "-I", self.script, "brief"] + list(args),
                           cwd=self.ws, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=env, timeout=60)
        return (p.returncode, p.stdout.decode("utf-8", "replace"),
                p.stderr.decode("utf-8", "replace"))

    def import_mod(self):
        import importlib
        sys.path.insert(0, os.path.dirname(self.script))
        try:
            sys.modules.pop("catalog", None)
            mod = importlib.import_module("catalog")
        finally:
            sys.path.pop(0)
        sys.modules.pop("catalog", None)
        return mod

    def call(self, name, read_only=False):
        import io
        mod = self.import_mod()
        buf, ebuf = io.StringIO(), io.StringIO()
        code = mod.run_brief(self.repo, self.ws, name, read_only, out=buf, err=ebuf)
        return code, buf.getvalue(), ebuf.getvalue()

    def lines(self, out):
        return out.rstrip("\n").split("\n")

    # --- 정상 출력 ---
    def test_none_both_rule_files_and_entry_docs(self):
        code, out, err = self.call("both")
        self.assertEqual(code, 0, err)
        root = os.path.join(self.ws, "both")
        self.assertIn(root, out)
        self.assertIn("both", self.lines(out)[0])
        claude = os.path.join(root, "CLAUDE.md")
        agents = os.path.join(root, "AGENTS.md")
        i_c, i_a = out.index(claude), out.index(agents)
        # entry_docs 는 적힌 순서대로, 절대 경로로
        tail = out[max(i_c, i_a):]
        e1 = tail.index(os.path.join(root, "README.md"))
        e2 = tail.index(os.path.join(root, "docs", "b.md"))
        e3 = tail.index(claude)
        self.assertTrue(e1 < e2 < e3, out)
        self.assertNotIn("민감도", out)
        self.assertNotIn("(없음 — 카드 확인 필요)", out)
        self.assertNotIn(READ_ONLY_LINE, out)
        self.assertIn("특이사항 없음", out)
        self.assertIn(".env", out)
        self.assertIn("기억 저장소", out)
        self.assertEqual(self.lines(out)[-1], BRIEF_LAST)

    def test_credentials_and_missing_entry_doc(self):
        code, out, err = self.call("cred")
        self.assertEqual(code, 0, err)
        root = os.path.join(self.ws, "cred")
        self.assertIn(os.path.join(root, "AGENTS.md"), out)
        rule_lines = [l for l in self.lines(out) if l.endswith("CLAUDE.md")]
        self.assertEqual(rule_lines, [], out)
        self.assertIn("카드 '주의' 절에 적힌 자격증명 파일은 열지 말 것", out)
        gone = [l for l in self.lines(out) if os.path.join(root, "gone.md") in l]
        self.assertEqual(len(gone), 1, out)
        self.assertIn("(없음 — 카드 확인 필요)", gone[0])
        readme = [l for l in self.lines(out) if l.endswith(os.path.join(root, "README.md"))]
        self.assertEqual(len(readme), 1, out)
        # 주의 절 전문
        self.assertIn("- `config.json` 에 자격증명이 있다. 열지 않는다.\n- 둘째 줄 주의 ZZ_CAUTION_END", out)
        self.assertEqual(self.lines(out)[-1], BRIEF_LAST)

    def test_only_claude(self):
        code, out, err = self.call("onlyclaude")
        self.assertEqual(code, 0, err)
        root = os.path.join(self.ws, "onlyclaude")
        self.assertIn(os.path.join(root, "CLAUDE.md"), out)
        self.assertNotIn(os.path.join(root, "AGENTS.md"), out)

    def test_pii_no_rule_files_read_scope_nfd(self):
        code, out, err = self.call("pii")
        self.assertEqual(code, 0, err)
        self.assertIn("CLAUDE.md·AGENTS.md 가 없다", out)
        self.assertIn("아래 하위폴더만 읽는다", out)
        self.assertIn("개인정보 파일을 열거나 인용하지 말 것", out)
        # 디스크의 실제(NFD) 이름으로 절대 경로
        self.assertIn(os.path.join(self.pii_real, self.sub_nfd), out)
        self.assertIn(os.path.join(self.pii_real, "README.md"), out)
        ghost = [l for l in self.lines(out) if l.rstrip().endswith("ghost") or "ghost " in l]
        self.assertEqual(len(ghost), 1, out)
        self.assertIn("(없음 — 카드 확인 필요)", ghost[0])
        self.assertNotIn("customers.csv", out)

    def test_read_only_on_any_card(self):
        code, out, err = self.call("both", read_only=True)
        self.assertEqual(code, 0, err)
        self.assertIn(READ_ONLY_LINE, out)
        self.assertEqual(self.lines(out)[-1], BRIEF_LAST)

    def test_done_status_ok(self):
        code, out, err = self.call("done")
        self.assertEqual(code, 0, err)

    # --- 상태별 오류 ---
    def test_superseded_default_exit_1_names_successor(self):
        code, out, err = self.run_cli("old")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("`both`", err)
        self.assertIn("--read-only", err)
        self.assertEqual(len(err.strip().split("\n")), 1, err)

    def test_superseded_read_only_exit_0(self):
        code, out, err = self.run_cli("old", "--read-only")
        self.assertEqual(code, 0, err)
        self.assertIn(os.path.join(self.ws, "old-folder"), out)
        self.assertIn(READ_ONLY_LINE, out)
        self.assertEqual(self.lines(out)[-1], BRIEF_LAST)

    def test_removed_exit_1(self):
        for extra in ((), ("--read-only",)):
            code, out, err = self.run_cli("gone", *extra)
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertIn("폴더가 없는 카드", err)

    def test_unknown_name_exit_1(self):
        code, out, err = self.run_cli("nosuch")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(len(err.strip().split("\n")), 1, err)

    def test_path_missing_exit_1(self):
        code, out, err = self.run_cli("nopath")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("missing-folder", err)

    def test_broken_target_card_one_line(self):
        with open(os.path.join(self.catalog, "broken.md"), "w", encoding="utf-8") as f:
            f.write(make_card("broken", "both", entry_docs="[secret/k.md]"))
        code, out, err = self.run_cli("broken")
        self.assertEqual(code, 1)
        self.assertEqual(len(err.strip().split("\n")), 1, err)
        self.assertNotIn("secret", err)

    def test_no_name_exit_2(self):
        code, out, err = self.run_cli()
        self.assertEqual(code, 2)

    def test_cli_matches_import(self):
        code, out, err = self.run_cli("cred")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, self.call("cred")[1])

    # --- 경계 ---
    def test_never_reads_workspace_file_content(self):
        import builtins
        import io
        mod = self.import_mod()
        opened = []
        real_open = builtins.open

        def spy(path, *a, **k):
            opened.append(os.path.realpath(path))
            return real_open(path, *a, **k)

        mod.open = spy
        for name in ("both", "cred", "pii", "onlyclaude"):
            buf = io.StringIO()
            code = mod.run_brief(self.repo, self.ws, name, True, out=buf, err=io.StringIO())
            self.assertEqual(code, 0)
            self.assertNotIn(MARK, buf.getvalue())
        allowed = (os.path.realpath(self.catalog) + os.sep,
                   os.path.realpath(os.path.join(self.repo, "machines")) + os.sep)
        outside = [p for p in opened if not p.startswith(allowed)]
        self.assertEqual(outside, [], outside)

    def test_other_machine_path_from_location_table(self):
        touch(os.path.join(self.ws, "elsewhere", "both", "CLAUDE.md"), MARK)
        os.makedirs(os.path.join(self.repo, "machines"))
        with open(os.path.join(self.repo, "machines", "other.md"), "w", encoding="utf-8") as f:
            f.write("# 맥 other\n\n| 카드 | 경로 |\n|---|---|\n| both | elsewhere/both |\n")
        code, out, err = self.run_cli("both", machine="other")
        self.assertEqual(code, 0, err)
        self.assertIn("폴더(절대 경로): %s" % os.path.join(self.ws, "elsewhere", "both"), out)
        self.assertIn(os.path.join(self.ws, "elsewhere", "both", "CLAUDE.md"), out)
        self.assertNotIn(MARK, out)
        code, out, err = self.run_cli("cred", machine="other")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("이 맥(other)에 폴더가 없음", err)

    def test_path_dash_card_exit_1(self):
        with open(os.path.join(self.catalog, "dash.md"), "w", encoding="utf-8") as f:
            f.write(make_card("dash", "-"))
        code, out, err = self.run_cli("dash")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("이 맥(main)에 폴더가 없음", err)

    def test_secret_not_exposed(self):
        for name in ("both", "cred", "pii", "onlyclaude", "old"):
            code, out, err = self.call(name, read_only=True)
            self.assertNotIn("secret", out + err)
            self.assertNotIn("token.txt", out)

    def test_brief_writes_nothing(self):
        def snapshot():
            snap = {}
            for d, dirs, files in os.walk(self.ws):
                for fn in dirs + files:
                    p = os.path.join(d, fn)
                    st = os.lstat(p)
                    snap[p] = (st.st_mtime_ns, st.st_size)
            return snap
        before = snapshot()
        for name in ("both", "cred", "pii", "old", "gone", "nopath", "nosuch"):
            self.run_cli(name)
            self.run_cli(name, "--read-only")
        after = snapshot()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
