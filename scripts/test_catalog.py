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
        self.write_card("alpha", make_card("alpha", "alpha", last_activity="2999-01-01",
                                           last_verified="2999-12-31"))
        code, out = self.build_and_check()
        self.assertEqual(code, 0, out)
        self.assertIn("last_activity: 2999-01-01\n", self.read("catalog/alpha.md").decode("utf-8"))
        self.assertNotIn("WARN alpha", out)

    def test_projects_same_on_every_machine(self):
        self.setup_other()
        self.run_script("build")
        first = self.read("PROJECTS.md")
        self.run_script("build", machine="other")
        self.assertEqual(first, self.read("PROJECTS.md"))


if __name__ == "__main__":
    unittest.main()
