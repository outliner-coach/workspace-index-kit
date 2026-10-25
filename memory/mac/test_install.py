"""install.sh·session-start.sh 단위 테스트.

HOME을 tempdir로 바꿔 install.sh를 실제로 실행한다. 실제 홈·서버·네트워크는 건드리지 않는다.
서버 테스트는 API 키가 없는 설정 파일 경로를 줘서 요청 자체가 나가지 않게 한다.
"""
import glob
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from memory.mac.test_hindsight_mem import card

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.realpath(os.path.join(HERE, "..", ".."))
INSTALL = os.path.join(HERE, "install.sh")
HOOK = os.path.join(HERE, "session-start.sh")
HOOK_CMD = "$HOME/.claude/hooks/hindsight-session-start.sh"


class InstallBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.claude = os.path.join(self.home, ".claude")
        self.settings = os.path.join(self.claude, "settings.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self, **extra):
        e = {"HOME": self.home, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        e.update(extra)
        return e

    def install(self):
        r = subprocess.run(["/bin/bash", INSTALL], env=self.env(), capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def load(self):
        with open(self.settings, encoding="utf-8") as f:
            return json.load(f)

    def ours(self, data):
        n = 0
        for entry in data["hooks"]["SessionStart"]:
            for h in entry.get("hooks", []):
                if h.get("command") == HOOK_CMD:
                    n += 1
        return n


class InstallTest(InstallBase):
    def test_links(self):
        self.install()
        mem = os.path.join(self.claude, "scripts", "hindsight-mem")
        hook = os.path.join(self.claude, "hooks", "hindsight-session-start.sh")
        self.assertTrue(os.path.islink(mem))
        self.assertTrue(os.path.islink(hook))
        self.assertEqual(os.readlink(mem), os.path.join(REPO, "memory", "mac", "hindsight_mem.py"))
        self.assertEqual(os.readlink(hook), os.path.join(REPO, "memory", "mac", "session-start.sh"))
        for d in (".local/state/hindsight", ".local/state/hindsight-queue"):
            self.assertTrue(os.path.isdir(os.path.join(self.home, d)))
        cfg = os.path.join(self.home, ".config", "hindsight")
        self.assertEqual(os.stat(cfg).st_mode & 0o777, 0o700)

    def test_launchd_plist_rendered(self):
        r = self.install()
        dst = os.path.join(self.home, "Library", "LaunchAgents",
                           "io.github.workspace-index.hindsight-pull.plist")
        with open(dst, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("__HOME__", text)
        self.assertNotIn("__LABEL__", text)
        self.assertIn(self.home + "/.claude/scripts/hindsight-pull-backups", text)
        r = self.install()
        self.assertIn("launchd: io.github.workspace-index.hindsight-pull 유지", r.stdout)

    def test_pull_backups_skips_without_remote(self):
        r = subprocess.run(["/bin/bash", os.path.join(HERE, "pull-backups.sh")],
                           env=self.env(HINDSIGHT_ENV_FILE=os.path.join(self.tmp, "none")),
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("HINDSIGHT_BACKUP_REMOTE 설정 없음", r.stdout)

    def test_wrong_link_replaced(self):
        scripts = os.path.join(self.claude, "scripts")
        os.makedirs(scripts)
        mem = os.path.join(scripts, "hindsight-mem")
        os.symlink("/nonexistent/elsewhere", mem)
        r = self.install()
        self.assertEqual(os.readlink(mem), os.path.join(REPO, "memory", "mac", "hindsight_mem.py"))
        self.assertIn("hindsight-mem", r.stdout)

    def test_settings_created(self):
        self.install()
        data = self.load()
        self.assertEqual(data["hooks"]["SessionStart"], [{
            "matcher": "startup|resume|clear",
            "hooks": [{"type": "command", "command": HOOK_CMD, "timeout": 15}],
        }])

    def test_existing_settings_preserved_and_idempotent(self):
        os.makedirs(self.claude)
        other = {"hooks": [{"type": "command", "command": "$HOME/x.sh || true", "timeout": 30}]}
        original = {
            "model": "opus",
            "permissions": {"allow": ["Bash(ls:*)"]},
            "theme": "한글값",
            "hooks": {"SessionStart": [other],
                      "Stop": [{"hooks": [{"type": "command", "command": "stop.sh"}]}]},
        }
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump(original, f, ensure_ascii=False, indent=2)
            f.write("\n")
        self.install()
        data = self.load()
        self.assertEqual(data["hooks"]["SessionStart"][0], other)
        self.assertEqual(len(data["hooks"]["SessionStart"]), 2)
        self.assertEqual(self.ours(data), 1)
        for k in ("model", "permissions", "theme"):
            self.assertEqual(data[k], original[k])
        self.assertEqual(data["hooks"]["Stop"], original["hooks"]["Stop"])
        with open(self.settings, encoding="utf-8") as f:
            raw = f.read()
        self.assertIn("한글값", raw)          # ensure_ascii=False
        self.assertTrue(raw.startswith('{\n  "'))  # 들여쓰기 2칸 유지
        backups = glob.glob(self.settings + ".bak-*")
        self.assertEqual(len(backups), 1)
        with open(backups[0], encoding="utf-8") as f:
            self.assertEqual(json.load(f), original)
        # 두 번째 실행: 항목은 그대로 하나, 결과 동일
        self.install()
        data2 = self.load()
        self.assertEqual(self.ours(data2), 1)
        self.assertEqual(data2, data)

    def test_env_file_permissions(self):
        r = self.install()
        envf = os.path.join(self.home, ".config", "hindsight", "env")
        self.assertFalse(os.path.exists(envf))
        self.assertIn("env", r.stdout)
        with open(envf, "w") as f:
            f.write("HINDSIGHT_API_KEY=secret-value-xyz\n")
        os.chmod(envf, 0o644)
        r = self.install()
        self.assertEqual(os.stat(envf).st_mode & 0o777, 0o600)
        self.assertNotIn("secret-value-xyz", r.stdout + r.stderr)


class HookTest(InstallBase):
    def setUp(self):
        super().setUp()
        self.ws = os.path.join(self.tmp, "ws")
        self.repo = os.path.join(self.ws, "workspace-index")
        cat = os.path.join(self.repo, "catalog")
        os.makedirs(cat)
        with open(os.path.join(cat, "alpha.md"), "w", encoding="utf-8") as f:
            f.write(card("alpha", "alpha"))
        os.makedirs(os.path.join(self.ws, "alpha"))
        os.makedirs(os.path.join(self.ws, "nocard"))
        self.state = os.path.join(self.tmp, "state")
        self.install()
        self.hook = os.path.join(self.claude, "hooks", "hindsight-session-start.sh")

    def run_hook(self, cwd):
        env = self.env(
            HINDSIGHT_REPO_ROOT=self.repo,
            HINDSIGHT_WORKSPACE_ROOT=self.ws,
            HINDSIGHT_STATE_DIR=self.state,
            HINDSIGHT_QUEUE_DIR=os.path.join(self.tmp, "queue"),
            HINDSIGHT_ENV_FILE=os.path.join(self.tmp, "no-env"),
            HINDSIGHT_URL="http://127.0.0.1:9",
        )
        payload = json.dumps({"cwd": cwd, "session_id": "t"})
        return subprocess.run([self.hook], input=payload, env=env, capture_output=True,
                              text=True, timeout=30)

    def test_executable(self):
        self.assertTrue(os.access(HOOK, os.X_OK))

    def test_no_bank_empty(self):
        r = self.run_hook(os.path.join(self.ws, "nocard"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_bank_context_server_down(self):
        r = self.run_hook(os.path.join(self.ws, "alpha"))
        self.assertEqual(r.returncode, 0)
        self.assertIn("저장 토큰:", r.stdout)
        self.assertIn("닿지 못했습니다", r.stdout)
        self.assertTrue(os.path.isdir(os.path.join(self.home, ".local", "state", "hindsight")))

    def test_broken_stdin_exit0(self):
        r = subprocess.run([self.hook], input="{oops", env=self.env(), capture_output=True,
                           text=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
