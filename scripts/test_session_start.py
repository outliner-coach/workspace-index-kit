"""session_start.py 테스트. 가짜 작업공간에 스크립트 복사본과 가짜 catalog.py를 두고 subprocess로 돌린다."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

FAKE_CATALOG = '''import sys
print("WARN a: alias `x` 가 다른 카드와 겹침: b")
print("WARN c: last_verified 2026-01-01 가 30일보다 오래됨")
print("ERROR d: path 없음")
print("카드 3장, 오류 1, 경고 2")
sys.exit(1)
'''


class SessionStartTest(unittest.TestCase):
    def setUp(self):
        self.ws = os.path.realpath(tempfile.mkdtemp())
        self.scripts = os.path.join(self.ws, "workspace-index", "scripts")
        os.makedirs(self.scripts)
        shutil.copy(os.path.join(HERE, "session_start.py"), self.scripts)
        with open(os.path.join(self.scripts, "catalog.py"), "w") as f:
            f.write(FAKE_CATALOG)
        os.makedirs(os.path.join(self.ws, "proj"))

    def tearDown(self):
        shutil.rmtree(self.ws)

    def run_hook(self, stdin, cwd=None, machine="other"):
        env = dict(os.environ, WORKSPACE_INDEX_MACHINE=machine)
        return subprocess.run(
            [sys.executable, os.path.join(self.scripts, "session_start.py")],
            input=stdin, capture_output=True, text=True, cwd=cwd or self.ws, timeout=30, env=env,
        )

    def test_root_prints_brief_and_check(self):
        p = self.run_hook(json.dumps({"cwd": self.ws}))
        self.assertEqual(p.returncode, 0)
        self.assertIn("오케스트레이터 모드", p.stdout)
        self.assertIn("카드 3장, 오류 1, 경고 2", p.stdout)
        self.assertIn("ERROR d: path 없음", p.stdout)
        self.assertIn("WARN c:", p.stdout)
        self.assertNotIn("WARN a:", p.stdout)
        self.assertIn("별칭 겹침 WARN 1줄은 생략", p.stdout)

    def test_brief_points_to_resolve_and_brief(self):
        p = self.run_hook(json.dumps({"cwd": self.ws}))
        self.assertIn("catalog.py resolve <키워드…>", p.stdout)
        self.assertIn("catalog.py brief <name>", p.stdout)
        self.assertIn("--read-only", p.stdout)

    def test_root_with_trailing_slash(self):
        p = self.run_hook(json.dumps({"cwd": self.ws + "/"}))
        self.assertIn("오케스트레이터 모드", p.stdout)

    def test_subfolder_is_silent(self):
        for cwd in (os.path.join(self.ws, "proj"), os.path.join(self.ws, "workspace-index")):
            p = self.run_hook(json.dumps({"cwd": cwd}))
            self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_bad_input_is_safe(self):
        for stdin in ("garbage", "[]", "null", json.dumps({"cwd": 3}), json.dumps({"cwd": "/no/such"})):
            p = self.run_hook(stdin, cwd=os.path.join(self.ws, "proj"))
            self.assertEqual((p.returncode, p.stdout), (0, ""), stdin)

    def test_machine_brief(self):
        p = self.run_hook(json.dumps({"cwd": self.ws}))
        self.assertIn("이 맥(`other`)의 위치표 workspace-index/machines/other.md가 없다", p.stdout)
        p = self.run_hook(json.dumps({"cwd": self.ws}), machine="main")
        self.assertIn("이 맥은 기본 맥 `main`이다", p.stdout)
        os.makedirs(os.path.join(self.ws, "workspace-index", "machines"))
        open(os.path.join(self.ws, "workspace-index", "machines", "other.md"), "w").close()
        p = self.run_hook(json.dumps({"cwd": self.ws}))
        self.assertIn("위치표 workspace-index/machines/other.md를 쓴다", p.stdout)
        self.assertIn(self.ws, p.stdout)

    def test_default_machine_from_config(self):
        with open(os.path.join(self.ws, "workspace-index", "config.json"), "w") as f:
            json.dump({"default_machine": "laptop"}, f)
        p = self.run_hook(json.dumps({"cwd": self.ws}), machine="laptop")
        self.assertIn("이 맥은 기본 맥 `laptop`이다", p.stdout)
        p = self.run_hook(json.dumps({"cwd": self.ws}))
        self.assertIn("카드 path는 기본 맥 laptop 기준이라", p.stdout)

    def test_not_a_git_repo_skips_sync(self):
        p = self.run_hook(json.dumps({"cwd": self.ws}))
        self.assertEqual(p.returncode, 0)
        self.assertIn("동기화를 건너뜀", p.stdout)

    def test_fast_forward_when_clean_and_behind(self):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

        def git(*args, cwd):
            subprocess.run(["git"] + list(args), cwd=cwd, env=env, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        index = os.path.join(self.ws, "workspace-index")
        remote = os.path.join(self.ws, "remote.git")
        other = os.path.join(self.ws, "other-clone")
        git("init", "-q", "--bare", remote, cwd=self.ws)
        git("init", "-q", cwd=index)
        git("add", ".", cwd=index)
        git("commit", "-q", "-m", "init", cwd=index)
        git("remote", "add", "origin", remote, cwd=index)
        git("push", "-q", "-u", "origin", "HEAD", cwd=index)
        git("clone", "-q", remote, other, cwd=self.ws)
        with open(os.path.join(other, "card.md"), "w") as f:
            f.write("x")
        git("add", ".", cwd=other)
        git("commit", "-q", "-m", "other mac", cwd=other)
        git("push", "-q", cwd=other)
        p = self.run_hook(json.dumps({"cwd": self.ws}))
        self.assertIn("커밋 1개를 받아 왔다", p.stdout)
        self.assertTrue(os.path.isfile(os.path.join(index, "card.md")))
        # 작업트리가 더러우면 받아 오지 않고 알리기만 한다
        with open(os.path.join(other, "card.md"), "w") as f:
            f.write("y")
        git("commit", "-q", "-am", "again", cwd=other)
        git("push", "-q", cwd=other)
        with open(os.path.join(index, "scripts", "catalog.py"), "a") as f:
            f.write("# 고치는 중\n")
        p = self.run_hook(json.dumps({"cwd": self.ws}))
        self.assertIn("0개 앞섬, 1개 뒤처짐", p.stdout)
        with open(os.path.join(index, "card.md")) as f:
            self.assertEqual(f.read(), "x")

    def test_empty_stdin_falls_back_to_process_cwd(self):
        p = self.run_hook("", cwd=self.ws)
        self.assertEqual(p.returncode, 0)
        self.assertIn("오케스트레이터 모드", p.stdout)


if __name__ == "__main__":
    unittest.main()
