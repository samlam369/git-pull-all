"""Offline integration tests: python3 -m unittest discover -s tests -v."""

import errno
import json
import os
from pathlib import Path
import pty
import subprocess
import tempfile
import threading
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
        for key in ("GPA_HOSTS_FILE", "SSH_FAIL_HOST", "SSH_FAIL_STATUS", "SSH_STDOUT",
                    "SSH_VERBOSE_STDOUT", "SSH_STDERR", "NO_COLOR", "GPA_COLOR", "GPA_PROGRESS"):
            self.env.pop(key, None)
        ssh = self.mock_bin / "ssh"
        ssh.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                       "with open(os.environ['SSH_LOG'], 'a') as f:\n"
                       "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                       "key = 'SSH_VERBOSE_STDOUT' if sys.argv[3].endswith(' gpa -v') else 'SSH_STDOUT'\n"
                       "sys.stdout.write(os.environ.get(key, ''))\n"
                       "sys.stderr.write(os.environ.get('SSH_STDERR', ''))\n"
                       "sys.exit(int(os.environ.get('SSH_FAIL_STATUS', '255'))\n"
                       "         if sys.argv[2] in os.environ.get('SSH_FAIL_HOST', '').split(',') else 0)\n")
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
                    self.assertEqual(call[2], "env GPA_COLOR=never GPA_PROGRESS=never gpa" + (" -v" if any("v" in flag for flag in flags) else ""))

    def test_remote_host_blocks_preserve_normal_and_verbose_output(self):
        self.hosts("first\nlast\n")
        self.env["SSH_STDOUT"] = "[CURRENT] ~/project  already up to date\ngpa summary: 1 repos\n"
        self.env["SSH_VERBOSE_STDOUT"] = "[CURRENT] ~/project\n    Branch: main\n    Result: already up to date\n\ngpa summary: 1 repos\n"
        for flags, key in ((("-a",), "SSH_STDOUT"), (("-av",), "SSH_VERBOSE_STDOUT")):
            with self.subTest(flags=flags):
                result = self.run_gpa(*flags)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                for index, host in enumerate(("first", "last"), 1):
                    self.assertIn(
                        f"[HOST   ] {host}  ({index}/2)\n"
                        + "".join(f"    {line}\n" if line else "\n"
                                  for line in self.env[key].splitlines()),
                        result.stdout,
                    )
                self.assertIn("\n\n[HOST   ] last", result.stdout)
                self.assertNotIn("[DONE", result.stdout)
                self.assertTrue(result.stdout.endswith(
                    "\ngpa hosts: 2/2 completed\n"))
                self.assertNotIn("\x1b[", result.stdout)

    def test_remote_failure_continues(self):
        self.hosts("first\nbroken\nlast\n")
        self.env["SSH_FAIL_HOST"] = "broken"
        for status in (255, 1):
            with self.subTest(status=status):
                self.env["SSH_FAIL_STATUS"] = str(status)
                self.log.unlink(missing_ok=True)
                result = self.run_gpa("-a")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual([call[1] for call in self.calls()], ["first", "broken", "last"])
                self.assertIn(f"    [FAILED ] broken  ssh or remote gpa failed (exit {status})\n", result.stdout)
                self.assertIn("[HOST   ] last  (3/3)\n", result.stdout)
                self.assertNotIn("[DONE", result.stdout)
                self.assertTrue(result.stdout.endswith(
                    "\ngpa hosts: 2/3 completed\n    Needs attention: broken\n"))
                self.assertEqual(result.stderr, "    gpa: failed on broken\n")

    def test_remote_diagnostics_keep_their_output_stream(self):
        self.hosts("broken\n")
        self.env.update(SSH_FAIL_HOST="broken", SSH_STDOUT="partial result",
                        SSH_STDERR="ssh: connection failed\n", TERM="xterm")
        result = self.run_gpa("-a")
        self.assertEqual(result.returncode, 1)
        self.assertIn("\n    partial result\n    [FAILED ]", result.stdout)
        self.assertEqual(result.stderr, "    ssh: connection failed\n    gpa: failed on broken\n")
        self.assertNotIn("\x1b[", result.stdout)

    def run_pty(self, script, *args, on_output=None):
        # Drain concurrently: verbose demo output can fill the PTY buffer and
        # block the child before it exits. Closing the slave gives EOF/EIO.
        master, slave = pty.openpty()
        chunks = []

        def drain():
            while True:
                try:
                    chunk = os.read(master, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                chunks.append(chunk)
                if on_output is not None:
                    on_output(b"".join(chunks))

        reader = threading.Thread(target=drain)
        reader.start()
        try:
            result = subprocess.run(
                ["bash", str(ROOT / script), *args], env=self.env,
                stdout=slave, stderr=subprocess.PIPE, text=True, timeout=30,
            )
        finally:
            os.close(slave)
            reader.join()
            os.close(master)
        return result, b"".join(chunks).decode().replace("\r\n", "\n")

    def mock_progress_pull(self):
        # Execute the real remote gpa through a pipe, as SSH would, while Git
        # waits for the test reader to acknowledge the visible FETCH status.
        (self.home / "repos/project/.git").mkdir(parents=True)
        (self.mock_bin / "gpa").symlink_to(ROOT / "bin/gpa")
        self.env["PULL_RELEASE"] = str(self.base / "release")
        ssh = self.mock_bin / "ssh"
        ssh.write_text(
            "#!/usr/bin/env python3\n"
            "import os, shlex, sys\n"
            "os.environ['GPA_PROGRESS'] = 'always'\n"
            "args = shlex.split(sys.argv[3])\n"
            "os.execvp(args[0], args)\n"
        )
        git = self.mock_bin / "git"
        git.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib, sys, time\n"
            "args = sys.argv[3:]\n"
            "if args == ['branch', '--show-current']:\n"
            "    print('main')\n"
            "elif args == ['rev-parse', '--abbrev-ref', '@{upstream}']:\n"
            "    print('origin/main')\n"
            "elif args == ['rev-parse', 'HEAD']:\n"
            "    print('aaaaaaa')\n"
            "elif args == ['pull', '--ff-only']:\n"
            "    deadline = time.monotonic() + 5\n"
            "    while not pathlib.Path(os.environ['PULL_RELEASE']).exists():\n"
            "        if time.monotonic() > deadline:\n"
            "            print('fatal: FETCH never reached the reader')\n"
            "            sys.exit(9)\n"
            "        time.sleep(0.01)\n"
            "    status = int(os.environ.get('PULL_STATUS', '0'))\n"
            "    print('fatal: simulated pull failure' if status else 'Already up to date.')\n"
            "    sys.exit(status)\n"
            "else:\n"
            "    sys.exit(2)\n"
        )
        git.chmod(0o755)

    def test_remote_fetch_is_visible_before_pull_finishes(self):
        self.hosts("example-host\n")
        self.mock_progress_pull()
        self.env.update(NO_COLOR="1", TERM="xterm")
        for flags in (("-a",), ("-av",)):
            for status in (0, 1):
                with self.subTest(flags=flags, status=status):
                    release = Path(self.env["PULL_RELEASE"])
                    release.unlink(missing_ok=True)
                    self.env["PULL_STATUS"] = str(status)
                    snapshots = []

                    def acknowledge_fetch(output):
                        if b"[FETCH  ] ~/repos/project (main)" in output and not snapshots:
                            snapshots.append(output)
                            release.touch()

                    result, output = self.run_pty("bin/gpa", *flags,
                                                  on_output=acknowledge_fetch)
                    self.assertTrue(snapshots, output)
                    self.assertNotIn(b"[CURRENT", snapshots[0])
                    self.assertNotIn(b"[FAILED", snapshots[0])
                    self.assertEqual(result.returncode, status, output)
                    final = "FAILED" if status else "CURRENT"
                    self.assertIn("\r\x1b[K    [FETCH  ] ~/repos/project (main)"
                                  f"\r\x1b[K    [{final}", output)
                    self.assertNotIn("FETCH never reached", output)
                    if status:
                        self.assertIn("fatal: simulated pull failure", output)
                        self.assertIn("gpa hosts: 0/1 completed", output)
                    else:
                        self.assertIn("gpa hosts: 1/1 completed", output)

    def test_piped_remote_output_disables_inherited_progress(self):
        self.hosts("example-host\n")
        self.mock_progress_pull()
        Path(self.env["PULL_RELEASE"]).touch()
        result = self.run_gpa("-a")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("    [CURRENT] ~/repos/project", result.stdout)
        self.assertNotIn("[FETCH", result.stdout)
        self.assertNotIn("\x1b[", result.stdout)

    def test_remote_disconnect_ends_pending_fetch_line(self):
        self.hosts("broken\n")
        self.env.update(SSH_FAIL_HOST="broken", NO_COLOR="1",
                        SSH_STDOUT="\r\x1b[K[FETCH  ] ~/repos/project (main)\n")
        result, output = self.run_pty("bin/gpa", "-a")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\r\x1b[K    [FETCH  ] ~/repos/project (main)\n"
                      "    [FAILED ] broken  ssh or remote gpa failed", output)
        self.assertIn("gpa hosts: 0/1 completed", output)

    def test_local_progress_policy(self):
        self.mock_progress_pull()
        Path(self.env["PULL_RELEASE"]).touch()
        self.env.update(NO_COLOR="1", GPA_PROGRESS="never")
        result, output = self.run_pty("bin/gpa")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("[FETCH", output)
        self.assertNotIn("\x1b[", output)
        self.env["GPA_PROGRESS"] = "always"
        result = subprocess.run(["bash", str(ROOT / "bin/gpa")],
                                env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"\r\x1b[K[FETCH  ] ~/repos/project (main)\n", result.stdout)
        self.assertIn(b"\r\x1b[K[CURRENT]", result.stdout)

    def test_remote_summary_color_respects_terminal_and_no_color(self):
        self.hosts("broken\n")
        self.env["SSH_FAIL_HOST"] = "broken"
        for term, no_color, colored in (("xterm", "", True),
                                        ("dumb", "", False),
                                        ("xterm", "1", False)):
            with self.subTest(term=term, no_color=no_color):
                self.env.update(TERM=term, NO_COLOR=no_color)
                self.log.unlink(missing_ok=True)
                result, output = self.run_pty("bin/gpa", "-a")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("gpa hosts: 0/1 completed", output)
                self.assertEqual(self.calls()[0][2],
                                 f"env GPA_COLOR={'always' if colored else 'never'} GPA_PROGRESS=always gpa")
                if colored:
                    self.assertIn("\x1b[31mNeeds attention: broken\x1b[0m", output)
                else:
                    self.assertNotIn("\x1b[", output)
                    self.assertIn("Needs attention: broken", output)

    def test_remote_attention_lists_hosts_in_configuration_order(self):
        self.hosts("zebra\nhealthy\nalpha\n")
        self.env["SSH_FAIL_HOST"] = "alpha,zebra"
        result = self.run_gpa("-a")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stdout.endswith(
            "\ngpa hosts: 1/3 completed\n    Needs attention: zebra, alpha\n"))

    def test_demo_color_and_progress_reach_host_output(self):
        for flags in ((), ("-v",)):
            for term, no_color, colored in (("xterm", "", True),
                                            ("dumb", "", False),
                                            ("xterm", "1", False)):
                with self.subTest(flags=flags, term=term, no_color=no_color):
                    self.env.update(TERM=term, NO_COLOR=no_color)
                    result, output = self.run_pty("examples/demo-output.sh", *flags)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn("\r\x1b[K    [FETCH", output)
                    if colored:
                        self.assertIn("\x1b[31m1 failed", output)
                        self.assertIn("\x1b[33m2 warnings\x1b[0m", output)
                        self.assertIn("\x1b[31mNeeds attention: server-mixed, server-offline\x1b[0m", output)
                    else:
                        self.assertNotIn("\x1b[", output.replace("\x1b[K", ""))
                        self.assertIn("2 warnings", output)

    def test_explicit_color_policy_for_piped_demo(self):
        for policy, no_color, colored in (("always", "", True),
                                          ("always", "1", False),
                                          ("never", "", False)):
            with self.subTest(policy=policy, no_color=no_color):
                self.env.update(GPA_COLOR=policy, NO_COLOR=no_color, TERM="dumb")
                result = subprocess.run(
                    ["bash", str(ROOT / "examples/demo-output.sh")],
                    env=self.env, text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual("\x1b[31m1 failed" in result.stdout, colored)
                self.assertEqual("\x1b[33m2 warnings" in result.stdout, colored)
                if not colored:
                    self.assertNotIn("\x1b[", result.stdout)

    def test_demo_covers_results_in_compact_and_verbose_modes(self):
        for flags in ((), ("-v",)):
            with self.subTest(flags=flags):
                result = subprocess.run(
                    ["bash", str(ROOT / "examples/demo-output.sh"), *flags],
                    env=self.env, text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                for status in ("UPDATED", "CURRENT", "SKIPPED", "FAILED"):
                    self.assertIn(f"    [{status}", result.stdout)
                for detail in ("remote forced update", "local changes restored",
                               "git warning", "detached@aaaaaaa", "no upstream"):
                    self.assertIn(detail, result.stdout)
                self.assertIn("    gpa summary: 0 repos", result.stdout)
                self.assertTrue(result.stdout.endswith(
                    "gpa hosts: 2/4 completed\n    Needs attention: server-mixed, server-offline\n"))
                self.assertEqual("        Branch:" in result.stdout, bool(flags))
                self.assertNotIn("\n\n\n", result.stdout)
                self.assertNotIn("\x1b[", result.stdout)
                self.assertEqual(self.calls(), [])

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
