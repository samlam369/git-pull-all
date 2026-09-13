"""Offline integration tests: python3 -m unittest discover -s tests -v."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GpaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.mock_bin = self.base / "mock-bin"
        self.mock_bin.mkdir()
        self.log = self.base / "ssh.jsonl"
        self.env = dict(os.environ, HOME=str(self.home),
                        XDG_CONFIG_HOME=str(self.home / ".config"),
                        PATH=f"{self.mock_bin}:{os.environ['PATH']}",
                        SSH_LOG=str(self.log), GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL="/dev/null")
        for key in ("GPA_HOSTS_FILE", "SSH_FAIL_HOST"):
            self.env.pop(key, None)
        ssh = self.mock_bin / "ssh"
        ssh.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                       "with open(os.environ['SSH_LOG'], 'a') as f:\n"
                       "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                       "sys.exit(255 if sys.argv[2] == os.environ.get('SSH_FAIL_HOST') else 0)\n")
        ssh.chmod(0o755)

    def run_gpa(self, *args):
        return subprocess.run(["bash", str(ROOT / "bin/gpa"), *args],
                              env=self.env, text=True, capture_output=True)

    def hosts(self, text):
        path = self.home / ".config/gpa/hosts"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_remote_host_order_and_verbose_options(self):
        self.hosts("# hosts\n\nnode-00\nuser@server.example\nNODE4")
        for flags in (("-a",), ("--all",), ("-av",), ("-va",), ("-a", "--verbose")):
            with self.subTest(flags=flags):
                self.log.unlink(missing_ok=True)
                result = self.run_gpa(*flags)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.calls()
                self.assertEqual([call[1] for call in calls], ["node-00", "user@server.example", "NODE4"])
                for call in calls:
                    self.assertEqual(call[0], "-q")
                    self.assertIn("whoami", call[2])
                    self.assertIn("hostname", call[2])
                    self.assertTrue(call[2].endswith("gpa -v" if any("v" in flag for flag in flags) else "gpa"))

    def test_remote_failure_continues(self):
        self.hosts("first\nbroken\nlast\n")
        self.env["SSH_FAIL_HOST"] = "broken"
        result = self.run_gpa("-a")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual([call[1] for call in self.calls()], ["first", "broken", "last"])

    def test_invalid_config_is_rejected_before_any_ssh(self):
        for content in (None, "", "# only a comment\n\n", "valid\n-oProxyCommand=evil\n", "valid\nhost other\n", "valid\nhost;echo\n"):
            with self.subTest(content=content):
                if content is not None:
                    self.hosts(content)
                result = self.run_gpa("-a")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.calls(), [])

    def test_hosts_override_and_home_fallback(self):
        self.hosts("default-host\n")
        self.env.pop("XDG_CONFIG_HOME")
        self.assertEqual(self.run_gpa("-a").returncode, 0)
        self.assertEqual(self.calls()[0][1], "default-host")
        alternate = self.base / "alternate-hosts"
        alternate.write_text("override-host\n")
        self.env["GPA_HOSTS_FILE"] = str(alternate)
        self.log.unlink()
        self.assertEqual(self.run_gpa("-a").returncode, 0)
        self.assertEqual(self.calls()[0][1], "override-host")

    def test_invalid_argument(self):
        for flag in ("-ax", "--unknown", "unexpected"):
            self.assertEqual(self.run_gpa(flag).returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_self_update_finishes_loaded_code(self):
        checkout = self.home / "repos/git-pull-all"
        (checkout / ".git").mkdir(parents=True)
        executable = checkout / "bin/gpa"
        executable.parent.mkdir()
        executable.write_bytes((ROOT / "bin/gpa").read_bytes())
        self.env["TEST_GPA_EXECUTABLE"] = str(executable)
        mock_git = self.mock_bin / "git"
        mock_git.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib, sys\n"
            "args = sys.argv[3:]\n"
            "target = pathlib.Path(os.environ['TEST_GPA_EXECUTABLE'])\n"
            "if args == ['branch', '--show-current']:\n"
            "    print('main')\n"
            "elif args == ['rev-parse', '--abbrev-ref', '@{upstream}']:\n"
            "    print('origin/main')\n"
            "elif args == ['rev-parse', 'HEAD']:\n"
            "    print('bbbbbbb' if 'new-version' in target.read_text() else 'aaaaaaa')\n"
            "elif args == ['pull', '--ff-only']:\n"
            "    target.write_text('#!/usr/bin/env bash\\nprintf \\\"new-version\\\\n\\\"\\n')\n"
            "    print('Updating aaaaaaa..bbbbbbb\\nFast-forward')\n"
            "else:\n"
            "    sys.exit(2)\n"
        )
        mock_git.chmod(0o755)
        result = subprocess.run(["bash", str(executable)], env=self.env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gpa summary: 1 repos", result.stdout)
        self.assertIn("1 updated", result.stdout)
        self.assertNotIn("new-version", result.stdout)
        result = subprocess.run(["bash", str(executable)], env=self.env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "new-version\n")

    def git(self, *args):
        return subprocess.run(["git", *map(str, args)], env=self.env,
                              text=True, capture_output=True, check=True).stdout.strip()

    def test_local_fast_forward_current_and_no_upstream(self):
        remote, seed = self.base / "remote.git", self.base / "seed"
        self.git("init", "--bare", remote)
        self.git("init", seed)
        self.git("-C", seed, "config", "user.email", "test@example.invalid")
        self.git("-C", seed, "config", "user.name", "Test")
        (seed / "file").write_text("first\n")
        self.git("-C", seed, "add", "file")
        self.git("-C", seed, "commit", "-m", "first")
        self.git("-C", seed, "remote", "add", "origin", remote)
        self.git("-C", seed, "push", "origin", "HEAD")
        branch = self.git("-C", seed, "branch", "--show-current")
        self.git("-C", remote, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
        checkout = self.home / "repos/project"
        checkout.parent.mkdir()
        self.git("clone", remote, checkout)
        self.git("init", self.home / ".no-upstream")
        (seed / "file").write_text("second\n")
        self.git("-C", seed, "commit", "-am", "second")
        self.git("-C", seed, "push", "origin", "HEAD")
        result = self.run_gpa()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((checkout / "file").read_text(), "second\n")
        self.assertIn("1 updated", result.stdout)
        self.assertIn("1 skipped", result.stdout)
        result = self.run_gpa("-v")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 current", result.stdout)
        self.assertIn("no upstream", result.stdout)
        self.assertEqual(self.calls(), [])

    def install(self, *args):
        return subprocess.run(["bash", str(ROOT / "install.sh"), *args],
                              env=self.env, text=True, capture_output=True)

    def test_install_is_idempotent(self):
        for _ in range(2):
            result = self.install()
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.home / ".local/bin/gpa").resolve(), ROOT / "bin/gpa")

    def test_install_migrates_only_known_dotfiles_link(self):
        link = self.home / ".local/bin/gpa"
        link.parent.mkdir(parents=True)
        old = self.home / ".dotfiles/shell-tools/.local/bin/gpa"
        old.parent.mkdir(parents=True)
        old.write_text("old implementation\n")
        link.symlink_to(old)
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(link.resolve(), old)
        result = self.install("--migrate-dotfiles")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(link.resolve(), ROOT / "bin/gpa")
        self.assertEqual(old.read_text(), "old implementation\n")

    def test_install_preserves_unrelated_files_and_links(self):
        link = self.home / ".local/bin/gpa"
        link.parent.mkdir(parents=True)
        for kind in ("file", "symlink"):
            with self.subTest(kind=kind):
                link.unlink(missing_ok=True)
                if kind == "file":
                    link.write_text("keep me\n")
                else:
                    link.symlink_to(self.base / "unrelated")
                result = self.install("--migrate-dotfiles")
                self.assertNotEqual(result.returncode, 0)
                if kind == "file":
                    self.assertEqual(link.read_text(), "keep me\n")
                else:
                    self.assertEqual(link.readlink(), self.base / "unrelated")


if __name__ == "__main__":
    unittest.main()
