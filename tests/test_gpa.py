"""Offline tests using temporary homes, Git fixtures, and mock SSH only."""

import importlib.util
import asyncio
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "bin/gpa-hosts.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("gpa_hosts", HELPER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GpaTests(unittest.TestCase):
    def test_live_and_final_host_headings_share_identity_and_order(self):
        helper = load_helper()
        for index in (0, 1):
            for lifecycle in ("starting", "running", "completed", "failed", "interrupted"):
                with self.subTest(index=index, lifecycle=lifecycle):
                    host = helper.HostState(index, "same-host", lifecycle=lifecycle)
                    heading = f"[HOST   ] same-host  ({index + 1}/2)"
                    self.assertEqual(helper.host_heading(host, 2), heading)
                    self.assertEqual(helper.host_heading(host, 2, live=True),
                                     f"{heading}  {lifecycle}")

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
        for key in ("GPA_HOSTS_FILE", "SSH_FAIL_HOST", "SSH_FAIL_STATUS",
                    "SSH_STDOUT", "SSH_VERBOSE_STDOUT", "SSH_STDERR",
                    "NO_COLOR", "GPA_COLOR", "GPA_PROGRESS",
                    "GPA_EVENT_STREAM", "BARRIER_DIR", "HOST_COUNT",
                    "RELEASE_FILE"):
            self.env.pop(key, None)
        self.write_ssh()

    def write_ssh(self, body=None):
        if body is None:
            body = (
                "host, command = sys.argv[4], sys.argv[5]\n"
                "key = 'SSH_VERBOSE_STDOUT' if command.endswith(' gpa -v') else 'SSH_STDOUT'\n"
                "sys.stdout.write(os.environ.get(key, '')); sys.stdout.flush()\n"
                "sys.stderr.write(os.environ.get('SSH_STDERR', '')); sys.stderr.flush()\n"
                "failed = host in os.environ.get('SSH_FAIL_HOST', '').split(',')\n"
                "sys.exit(int(os.environ.get('SSH_FAIL_STATUS', '255')) if failed else 0)\n")
        path = self.mock_bin / "ssh"
        path.write_text(
            "#!/usr/bin/env python3\nimport json, os, pathlib, sys, time\n"
            "with open(os.environ['SSH_LOG'], 'a') as stream:\n"
            " stream.write(json.dumps(sys.argv[1:]) + '\\n')\n" + body)
        path.chmod(0o755)

    def hosts(self, text):
        path = self.home / ".config/gpa/hosts"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def run_gpa(self, *args, timeout=30):
        return subprocess.run(["bash", str(ROOT / "bin/gpa"), *args],
                              env=self.env, text=True, capture_output=True,
                              timeout=timeout)

    def calls(self):
        return ([json.loads(line) for line in self.log.read_text().splitlines()]
                if self.log.exists() else [])

    def test_remote_options_use_fixed_noninteractive_command(self):
        self.hosts("node-00\nuser@server.example\nNODE4\n")
        for flags in (("-a",), ("--all",), ("-av",), ("-va",),
                      ("-a", "--verbose")):
            with self.subTest(flags=flags):
                self.log.unlink(missing_ok=True)
                result = self.run_gpa(*flags)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.calls()
                self.assertCountEqual([call[3] for call in calls],
                                      ["node-00", "user@server.example", "NODE4"])
                for call in calls:
                    self.assertEqual(call[:3], ["-q", "-o", "BatchMode=yes"])
                    expected = ("env GPA_COLOR=never GPA_PROGRESS=never "
                                "GPA_EVENT_STREAM=1 gpa")
                    if any("v" in flag for flag in flags):
                        expected += " -v"
                    self.assertEqual(call[4], expected)

    def test_ssh_workers_cannot_consume_dispatcher_input(self):
        self.hosts("reader-one\nreader-two\n")
        self.write_ssh(
            "data = sys.stdin.buffer.read()\n"
            "print('stdin-eof' if not data else 'stdin-leaked', flush=True)\n")
        result = subprocess.run(
            ["bash", str(ROOT / "bin/gpa"), "-a"], env=self.env,
            input="reserved-for-the-dispatcher", text=True,
            capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("stdin-leaked", result.stdout)
        for host in ("reader-one", "reader-two"):
            self.assertIn(f"[{host}] stdin-eof", result.stdout)

    def test_all_hosts_cross_launch_barrier_before_finishing(self):
        destinations = ["alpha", "beta", "gamma", "delta"]
        self.hosts("\n".join(destinations) + "\n")
        barrier = self.base / "barrier"
        barrier.mkdir()
        self.env.update(BARRIER_DIR=str(barrier), HOST_COUNT="4")
        self.write_ssh(
            "host = sys.argv[4]; barrier = pathlib.Path(os.environ['BARRIER_DIR'])\n"
            "(barrier / ('started-' + host)).touch()\n"
            "deadline = time.monotonic() + 5\n"
            "while len(list(barrier.glob('started-*'))) < int(os.environ['HOST_COUNT']):\n"
            " time.sleep(.01)\n"
            " if time.monotonic() > deadline: sys.exit(90)\n"
            "print('ready', flush=True)\n")
        result = self.run_gpa("-a")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p.name for p in barrier.iterdir()},
                         {f"started-{host}" for host in destinations})

    def test_two_running_hosts_stream_before_release(self):
        self.hosts("alpha\nbeta\n")
        release = self.base / "release"
        self.env["RELEASE_FILE"] = str(release)
        self.write_ssh(
            "host = sys.argv[4]; print('live-' + host, flush=True)\n"
            "release = pathlib.Path(os.environ['RELEASE_FILE'])\n"
            "deadline = time.monotonic() + 5\n"
            "while not release.exists():\n"
            " time.sleep(.01)\n"
            " if time.monotonic() > deadline: sys.exit(91)\n"
            "print('done-' + host, flush=True)\n")
        process = subprocess.Popen(["bash", str(ROOT / "bin/gpa"), "-a"],
                                   env=self.env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        seen = b""
        deadline = time.monotonic() + 5
        while not all(f"live-{host}".encode() in seen
                      for host in ("alpha", "beta")):
            self.assertLess(time.monotonic(), deadline)
            ready, _, _ = select.select([process.stdout], [], [], .1)
            if ready:
                seen += os.read(process.stdout.fileno(), 65536)
        self.assertIsNone(process.poll())
        self.assertNotIn(b"done-", seen)
        release.touch()
        stdout, stderr = process.communicate(timeout=10)
        output = (seen + stdout).decode()
        self.assertEqual(process.returncode, 0, stderr.decode())
        self.assertIn("[alpha] done-alpha", output)
        self.assertIn("[beta] done-beta", output)

    def test_plain_attributes_blank_partial_and_diagnostics(self):
        self.hosts("first\nbroken\n")
        self.env.update(SSH_STDOUT="result\n\nunterminated",
                        SSH_STDERR="ssh: diagnostic\n",
                        SSH_FAIL_HOST="broken", SSH_FAIL_STATUS="255")
        result = self.run_gpa("-a")
        self.assertEqual(result.returncode, 1)
        for host in ("first", "broken"):
            self.assertIn(f"[{host}] result\n", result.stdout)
            self.assertIn(f"[{host}] \n", result.stdout)
            self.assertIn(f"[{host}] unterminated\n", result.stdout)
            self.assertIn(f"[{host}] ssh: diagnostic", result.stderr)
        self.assertIn("[broken] [FAILED ] broken", result.stdout)
        self.assertTrue(result.stdout.endswith(
            "gpa hosts: 1/2 completed\n    Needs attention: broken\n"))
        self.assertNotIn("\x1b[", result.stdout + result.stderr)

    def test_large_stderr_is_drained_with_stdout(self):
        self.hosts("noisy\n")
        self.write_ssh(
            "sys.stderr.write('diagnostic\\n' * 20000); sys.stderr.flush()\n"
            "print('L' + 'x' * 200000 + 'R', flush=True)\n"
            "print('stdout survived', flush=True)\n")
        result = self.run_gpa("-a")
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        self.assertIn("[noisy] stdout survived", result.stdout)
        self.assertIn("[noisy] L" + "x" * 200000 + "R\n", result.stdout)
        self.assertEqual(result.stderr.count("[noisy] diagnostic"), 20000)

    def test_failed_hosts_do_not_stop_slow_success(self):
        self.hosts("failed\nslow\npull-failed\n")
        self.write_ssh(
            "host = sys.argv[4]\n"
            "if host == 'failed':\n print('connection refused', file=sys.stderr, flush=True); sys.exit(255)\n"
            "if host == 'slow':\n print('slow result', flush=True); time.sleep(.1); sys.exit(0)\n"
            "print('[FAILED ] repo pull', flush=True); sys.exit(1)\n")
        result = self.run_gpa("-a")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[slow] slow result", result.stdout)
        self.assertIn("gpa hosts: 1/3 completed", result.stdout)
        self.assertIn("Needs attention: failed, pull-failed", result.stdout)

    def test_duplicate_hosts_are_distinct_workers(self):
        self.hosts("same\nsame\n")
        result = self.run_gpa("-a")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(result.stdout.count("[same] [HOST STARTING]"), 2)

    def test_fetch_replacement_is_host_and_repo_scoped(self):
        module = load_helper()
        alpha, beta = module.HostState(0, "alpha"), module.HostState(1, "beta")
        def event(kind, text):
            return module.EVENT_PREFIX + json.dumps(
                {"v": 1, "type": kind, "repo": "~/repo", "text": text})
        alpha.ingest("stdout", event("fetch", "fetch-alpha"))
        beta.ingest("stdout", event("fetch", "fetch-beta"))
        alpha.ingest("stdout", event("result", "result-alpha"))
        self.assertEqual([r.text for r in alpha.records], ["result-alpha"])
        self.assertEqual([r.text for r in beta.records], ["fetch-beta"])

    def test_scroll_anchor_tracks_host_when_earlier_section_grows(self):
        module = load_helper()
        anchor = module.logical_scroll_anchor(
            12, [("host-0", 0, 10), ("host-1", 10, 30)])
        self.assertEqual(anchor, ("host-1", 2))
        self.assertEqual(module.restore_scroll_y(
            anchor, {"host-0": 0, "host-1": 24}, 12), 26)

    def test_legacy_malformed_and_control_output_stays_text(self):
        module = load_helper()
        host = module.HostState(0, "old")
        host.ingest("stdout", "\r\x1b[K[FETCH  ] ~/legacy")
        host.ingest("stdout", module.EVENT_PREFIX + "not-json")
        self.assertEqual(host.records[0].text, "[FETCH  ] ~/legacy")
        self.assertIn("GPA1 not-json", host.records[1].text)
        self.assertNotIn("\x1b", "".join(r.text for r in host.records))

    def test_supporting_remote_event_stream_is_normalized(self):
        (self.home / "repos/project/.git").mkdir(parents=True)
        (self.mock_bin / "gpa").symlink_to(ROOT / "bin/gpa")
        self.mock_git_current()
        self.hosts("new-host\n")
        self.write_ssh("import shlex\nargs = shlex.split(sys.argv[5])\nos.execvp(args[0], args)\n")
        result = self.run_gpa("-a")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[new-host] [CURRENT] ~/repos/project", result.stdout)
        self.assertNotIn("GPA1", result.stdout)
        self.assertNotIn("[FETCH", result.stdout)

    def test_offline_demo_covers_overlap_results_and_verbose_details(self):
        for flags in ((), ("-v",)):
            with self.subTest(flags=flags):
                result = subprocess.run(
                    ["bash", str(ROOT / "examples/demo-output.sh"), *flags],
                    env=dict(self.env, NO_COLOR="1", GPA_PROGRESS="always"),
                    text=True, capture_output=True, timeout=15,
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                for host in ("server-ok", "server-mixed", "server-offline",
                             "server-empty"):
                    self.assertIn(f"[{host}] [HOST STARTING]", result.stdout)
                for status in ("UPDATED", "CURRENT", "SKIPPED", "FAILED"):
                    self.assertIn(f"] [{status}", result.stdout)
                self.assertIn("Needs attention: server-mixed, server-offline",
                              result.stdout)
                self.assertEqual("Branch:" in result.stdout, bool(flags))
                self.assertEqual(self.calls(), [])

    def test_invalid_config_stops_before_ssh(self):
        for content in (None, "", "# comment\n", "valid\n-oProxyCommand=x\n",
                        "valid\nhost other\n", "valid\nhost;echo\n"):
            with self.subTest(content=content):
                if content is not None:
                    self.hosts(content)
                result = self.run_gpa("-a")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.calls(), [])
                self.log.unlink(missing_ok=True)

    def test_missing_dispatcher_stops_before_ssh(self):
        isolated = self.base / "isolated/bin"
        isolated.mkdir(parents=True)
        copy = isolated / "gpa"
        copy.write_bytes((ROOT / "bin/gpa").read_bytes())
        self.hosts("unused\n")
        result = subprocess.run(["bash", str(copy), "-a"], env=self.env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("requires Python 3.10+", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_override_and_invalid_arguments(self):
        alternate = self.base / "hosts"
        alternate.write_text("override-host\n")
        self.env["GPA_HOSTS_FILE"] = str(alternate)
        self.assertEqual(self.run_gpa("-a").returncode, 0)
        self.assertEqual(self.calls()[0][3], "override-host")
        self.log.unlink()
        for flag in ("-ax", "--unknown", "unexpected"):
            result = self.run_gpa(flag)
            self.assertEqual(result.returncode, 2)
            self.assertIn(flag, result.stderr)
            self.assertIn("gpa --help", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_help_explains_local_and_configured_host_scope_without_work(self):
        # Help must work even when remote configuration is unavailable. Trap
        # Git calls as well as SSH so help can safely be used as a setup check.
        git = self.mock_bin / "git"
        git.write_text("#!/bin/sh\necho unexpected-git-call >&2\nexit 99\n")
        git.chmod(0o755)
        for flag in ("-h", "--help"):
            result = self.run_gpa("-a", flag)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            for phrase in ("By default, update this machine", "-a, --all",
                           "configured SSH hosts concurrently instead",
                           "only if listed as a destination", "GPA_HOSTS_FILE",
                           "-v, --verbose", "-h, --help", "gpa -av"):
                self.assertIn(phrase, result.stdout)
        self.assertEqual(self.calls(), [])

    def run_full_pty(self, *args):
        master, slave = pty.openpty()
        process = subprocess.Popen(["bash", str(ROOT / "bin/gpa"), *args],
                                   env=self.env, stdin=slave, stdout=slave,
                                   stderr=slave)
        os.close(slave)
        chunks = []
        while True:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        process.wait(timeout=10)
        os.close(master)
        return process.returncode, b"".join(chunks).decode(errors="replace")

    def test_missing_textual_on_full_terminal_launches_no_ssh(self):
        try:
            import textual  # noqa: F401
        except ImportError:
            pass
        else:
            self.skipTest("Textual installed; covered by live UI test environment")
        self.hosts("unused\n")
        status, output = self.run_full_pty("-a")
        self.assertEqual(status, 1, output)
        self.assertIn("requires Textual", output)
        self.assertEqual(self.calls(), [])

    def test_live_ui_handles_overflow_page_scroll_and_resize(self):
        try:
            import textual  # noqa: F401
        except ImportError:
            self.skipTest("Textual is not installed")
        import fcntl
        import struct
        import termios
        self.hosts("overflow\n")
        release = self.base / "release"
        emitted = self.base / "emitted"
        self.env.update(RELEASE_FILE=str(release), BARRIER_DIR=str(emitted))
        self.write_ssh(
            "print('\\n'.join('line-%03d-' % n + 'x' * 100 for n in range(80)), flush=True)\n"
            "pathlib.Path(os.environ['BARRIER_DIR']).touch()\n"
            "release = pathlib.Path(os.environ['RELEASE_FILE'])\n"
            "while not release.exists(): time.sleep(.01)\n")
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 18, 60, 0, 0))
        process = subprocess.Popen(["bash", str(ROOT / "bin/gpa"), "-a"],
                                   env=self.env, stdin=slave, stdout=slave,
                                   stderr=slave, start_new_session=True)
        os.close(slave)
        chunks = []
        def drain():
            while True:
                try:
                    chunks.append(os.read(master, 65536))
                except OSError:
                    return
        reader = threading.Thread(target=drain)
        reader.start()
        deadline = time.monotonic() + 8
        while not emitted.exists():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)
        # Let one coalesced refresh render before exercising navigation.
        time.sleep(.2)
        os.write(master, b"\x1b[5~")  # Page Up while earlier content is stable.
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 28, 100, 0, 0))
        os.killpg(process.pid, signal.SIGWINCH)
        release.touch()
        self.assertEqual(process.wait(timeout=10), 0)
        reader.join(timeout=3); os.close(master)
        output = b"".join(chunks).decode(errors="replace")
        # The retained report proves overflow content survives UI scrolling and
        # resizing, while alternate-screen teardown restores normal scrollback.
        self.assertIn("[HOST   ] overflow  (1/1)", output)
        self.assertIn("line-000-", output); self.assertIn("line-079-", output)
        self.assertIn("\x1b[?1049h", output); self.assertIn("\x1b[?1049l", output)
        # Inspect actual live terminal output, not just the final plain report.
        # Native defaults must survive rendering instead of becoming a dark
        # RGB palette, regardless of the terminal's own light/dark colors.
        live = output.split("\x1b[?1049h", 1)[1].split("\x1b[?1049l", 1)[0]
        self.assertIn("[HOST   ] overflow  (1/1)  running",
                      re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", live))
        self.assertRegex(live, r"\x1b\[[0-9;]*49[;m]")
        for sgr in re.findall(r"\x1b\[([0-9;]*)m", live):
            self.assertNotRegex(sgr, r"(?:^|;)(?:38|48);(?:2|5);")
            # The visible scrollbar follows text, not Textual's ANSI blue.
            self.assertNotRegex(sgr, r"(?:^|;)(?:34|94)(?:;|$)")

    def test_live_pty_navigation_with_stdin_reading_ssh(self):
        self.check_live_pty_navigation()

    def test_live_pty_navigation_without_initial_in_band_size(self):
        self.check_live_pty_navigation(missing_initial_size=True)

    def check_live_pty_navigation(self, missing_initial_size=False):
        try:
            import textual  # noqa: F401
        except ImportError:
            self.skipTest("Textual is not installed")
        import fcntl
        import struct
        import termios
        self.hosts("input-reader\n")
        release = self.base / "release"
        self.env.update(RELEASE_FILE=str(release), TERM="xterm-256color")
        # Real SSH forwards stdin even with BatchMode. Exercise that behavior,
        # which output-only mocks and headless renderer tests cannot detect.
        self.write_ssh(
            "if sys.stdin.isatty():\n"
            " print('ERROR-shared-terminal-stdin', flush=True); sys.exit(91)\n"
            "assert sys.stdin.buffer.read() == b''\n"
            "print('\\n'.join('row-%03d' % n for n in range(100)), flush=True)\n"
            "while not pathlib.Path(os.environ['RELEASE_FILE']).exists(): time.sleep(.01)\n")
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 18, 80, 0, 0))
        process = subprocess.Popen(["bash", str(ROOT / "bin/gpa"), "-a"],
                                   env=self.env, stdin=slave, stdout=slave,
                                   stderr=slave, start_new_session=True)
        os.close(slave)
        output = bytearray()
        replied = False

        def wait_for(marker, since=0):
            nonlocal replied
            deadline = time.monotonic() + 5
            while marker not in output[since:]:
                self.assertNotIn(b"ERROR-shared-terminal-stdin", output)
                self.assertLess(time.monotonic(), deadline,
                                bytes(output[-2000:]).decode(errors="replace"))
                if select.select([master], [], [], .05)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        self.fail("live view exited before rendering " + repr(marker))
                    self.assertTrue(chunk)
                    output.extend(chunk)
                    if (missing_initial_size and not replied
                            and b"\x1b[?2048$p" in output):
                        # T3 0.0.40's Ghostty core advertises support but sends
                        # no initial CSI 48 dimensions until a physical resize.
                        os.write(master, b"\x1b[?2048;2$y")
                        replied = True

        try:
            wait_for(b"row-014")
            self.assertNotIn(b"row-025", output)
            if missing_initial_size:
                if replied:
                    wait_for(b"\x1b[?1016h")
                # Mouse clicks are in pixels once mode 1016 is enabled, in
                # cells otherwise. An out-of-bounds decoded event also clears
                # Textual keyboard focus, reproducing all three broken inputs.
                mouse = (b"\x1b[<0;90;90M\x1b[<0;90;90m"
                         if b"\x1b[?1016h" in output
                         else b"\x1b[<0;10;5M\x1b[<0;10;5m")
                os.write(master, mouse)
            # Read real rendered rows after actual terminal escape sequences;
            # final scrollback cannot satisfy these checks while SSH is held.
            start = len(output)
            os.write(master, b"\x1b[6~")
            wait_for(b"row-025", start)
            start = len(output)
            os.write(master, b"\x1b[F")
            wait_for(b"row-099", start)
            start = len(output)
            os.write(master, b"\x1b[H")
            wait_for(b"row-000", start)
            start = len(output)
            # SGR mouse wheel down over the report text, not its container API.
            os.write(master, b"\x1b[<65;10;5M" * 4)
            wait_for(b"row-020", start)
            if missing_initial_size:
                self.assertNotIn(b"\x1b[?1016h", output)
                # The compatibility path must still react to normal PTY
                # resize signals and keep navigation working afterward.
                fcntl.ioctl(master, termios.TIOCSWINSZ,
                            struct.pack("HHHH", 20, 80, 0, 0))
                os.killpg(process.pid, signal.SIGWINCH)
                start = len(output)
                os.write(master, b"\x1b[H")
                wait_for(b"row-000", start)
            self.assertIsNone(process.poll())
            release.touch()
            wait_for(b"gpa hosts: 1/1 completed")
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            release.touch()
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            os.close(master)

    def test_live_scroll_navigation_and_growth_keep_reader_position(self):
        try:
            from textual.app import App
            from textual import events
        except ImportError:
            self.skipTest("Textual is not installed")
        helper = load_helper()
        captured = []
        # Capture the real app, then run its renderer/event loop headlessly.
        # Only the SSH worker is replaced; navigation and layout remain real.
        with patch.object(App, "run", lambda app, **kwargs: captured.append(app)):
            helper.run_live(["early", "later"], "unused", False)
        app = captured[0]

        async def idle_worker(dispatcher):
            await asyncio.Event().wait()

        async def exercise():
            with patch.object(helper.Dispatcher, "run", idle_worker):
                async with app.run_test(size=(80, 18)) as pilot:
                    for host in app.dispatcher.hosts:
                        for n in range(70):
                            app.receive(host, host.ingest("stdout", f"row-{n}"))
                    await pilot.pause(.25)
                    report = app.query_one("#report")
                    self.assertIs(app.focused, report)
                    # Keep real redraws active throughout keyboard and mouse
                    # input, without changing report height during navigation.
                    updates = 0
                    def keep_updating():
                        nonlocal updates
                        updates += 1
                        host = app.dispatcher.hosts[0]
                        host.records[0].text = f"tick-{updates:06d}"
                        app.receive(host, host.records[0])
                    ticker = app.set_interval(.05, keep_updating)
                    await pilot.press("pagedown")
                    await pilot.pause(.4)
                    self.assertGreater(report.scroll_y, 5)
                    await pilot.press("end")
                    await pilot.pause(.4)
                    self.assertAlmostEqual(report.scroll_y, report.max_scroll_y)
                    await pilot.press("pageup")
                    await pilot.pause(.4)
                    self.assertLess(report.scroll_y, report.max_scroll_y - 5)

                    # Hold a row in the second host while the earlier host grows.
                    later = app.query_one("#host-1")
                    report.scroll_to(y=later.virtual_region.y + 10,
                                     animate=False, immediate=True)
                    await pilot.pause(.2)
                    offset = report.scroll_y - later.virtual_region.y
                    for n in range(5):
                        host = app.dispatcher.hosts[0]
                        app.receive(host, host.ingest("stdout", f"new-{n}"))
                        await pilot.pause(.15)
                        self.assertAlmostEqual(
                            report.scroll_y - later.virtual_region.y, offset)

                    before = report.scroll_y
                    report.post_message(events.MouseScrollDown(
                        report, 2, 2, 0, 0, 0, False, False, False))
                    await pilot.pause(.4)
                    self.assertGreater(report.scroll_y, before)
                    before = report.scroll_y
                    report.post_message(events.MouseScrollUp(
                        report, 2, 2, 0, 0, 0, False, False, False))
                    await pilot.pause(.4)
                    self.assertLess(report.scroll_y, before)
                    before = report.scroll_y
                    await pilot.press("down")
                    await pilot.pause(.3)
                    self.assertAlmostEqual(report.scroll_y, before + 1)
                    await pilot.press("up")
                    await pilot.pause(.3)
                    self.assertAlmostEqual(report.scroll_y, before)
                    await pilot.resize_terminal(60, 12)
                    await pilot.pause(.2)
                    self.assertAlmostEqual(report.scroll_y, before)

                    # Simulate input arriving after the anchor snapshot but
                    # before its post-layout callback. The newer input wins.
                    ticker.stop()
                    await pilot.pause(.2)
                    callbacks = []
                    host = app.dispatcher.hosts[0]
                    app.receive(host, host.ingest("stdout", "another-row"))
                    with patch.object(app, "call_after_refresh",
                                      lambda callback: callbacks.append(callback)):
                        app.redraw_dirty()
                    report.scroll_to(y=before + 3, animate=False, immediate=True)
                    await pilot.pause(.2)
                    self.assertTrue(callbacks)
                    for callback in callbacks:
                        callback()
                    self.assertAlmostEqual(report.scroll_y, before + 3)
                    await pilot.press("home")
                    await pilot.pause(.4)
                    self.assertEqual(report.scroll_y, 0)
                    app.runner.cancel()
        asyncio.run(exercise())

    def test_live_terminal_palette_with_and_without_color(self):
        try:
            import textual  # noqa: F401
        except ImportError:
            self.skipTest("Textual is not installed")
        self.hosts("palette-fixture\n")
        self.write_ssh("print('1 failed | 1 warnings', flush=True)\ntime.sleep(.2)\n")
        for no_color in (False, True):
            with self.subTest(no_color=no_color):
                self.env.update(TERM="xterm-256color", GPA_COLOR="always")
                if no_color:
                    self.env["NO_COLOR"] = "1"
                else:
                    self.env.pop("NO_COLOR", None)
                status, output = self.run_full_pty("-a")
                self.assertEqual(status, 0, output)
                live = output.split("\x1b[?1049h", 1)[1].split("\x1b[?1049l", 1)[0]
                sgrs = re.findall(r"\x1b\[([0-9;]*)m", live)
                for sgr in sgrs:
                    self.assertNotRegex(sgr, r"(?:^|;)(?:38|48);(?:2|5);")
                has_status_color = any(
                    re.search(r"(?:^|;)(?:31|33)(?:;|$)", sgr) for sgr in sgrs)
                self.assertEqual(has_status_color, not no_color)

    def test_plain_sigint_returns_130_and_reaps_workers(self):
        self.hosts("slow-a\nslow-b\n")
        barrier = self.base / "barrier"
        barrier.mkdir()
        self.env["BARRIER_DIR"] = str(barrier)
        self.write_ssh(
            "host = sys.argv[4]; (pathlib.Path(os.environ['BARRIER_DIR']) / host).touch()\n"
            "print('began', flush=True)\nwhile True: time.sleep(1)\n")
        process = subprocess.Popen(["bash", str(ROOT / "bin/gpa"), "-a"],
                                   env=self.env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True,
                                   start_new_session=True)
        deadline = time.monotonic() + 5
        while len(list(barrier.iterdir())) < 2:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        os.killpg(process.pid, signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 130, stderr)
        self.assertIn("Needs attention: slow-a, slow-b", stdout)

    def test_live_ctrl_c_restores_terminal_and_retains_partial_report(self):
        try:
            import textual  # noqa: F401
        except ImportError:
            self.skipTest("Textual is not installed")
        self.hosts("interactive-slow\n")
        started = self.base / "started"
        self.env["BARRIER_DIR"] = str(started)
        self.write_ssh(
            "pathlib.Path(os.environ['BARRIER_DIR']).touch()\n"
            "print('partial result', flush=True)\nwhile True: time.sleep(1)\n")
        master, slave = pty.openpty()
        process = subprocess.Popen(["bash", str(ROOT / "bin/gpa"), "-a"],
                                   env=self.env, stdin=slave, stdout=slave,
                                   stderr=slave, start_new_session=True)
        os.close(slave)
        chunks = []
        def drain():
            while True:
                try:
                    chunks.append(os.read(master, 65536))
                except OSError:
                    return
        reader = threading.Thread(target=drain); reader.start()
        deadline = time.monotonic() + 8
        while not started.exists():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)
        os.write(master, b"\x03")
        self.assertEqual(process.wait(timeout=10), 130)
        reader.join(timeout=3); os.close(master)
        output = b"".join(chunks).decode(errors="replace")
        self.assertIn("partial result", output)
        self.assertIn("[INTERRUPTED] host did not finish", output)
        self.assertIn("\x1b[?1049l", output)

    def mock_git_current(self):
        path = self.mock_bin / "git"
        path.write_text(
            "#!/usr/bin/env python3\nimport sys\na = sys.argv[3:]\n"
            "if a == ['branch', '--show-current']: print('main')\n"
            "elif a == ['rev-parse', '--abbrev-ref', '@{upstream}']: print('origin/main')\n"
            "elif a == ['rev-parse', 'HEAD']: print('aaaaaaa')\n"
            "elif a == ['pull', '--ff-only']: print('Already up to date.')\n"
            "else: sys.exit(2)\n")
        path.chmod(0o755)

    def test_local_behavior_stays_bash_only(self):
        (self.home / "repos/project/.git").mkdir(parents=True)
        self.mock_git_current()
        self.env.update(NO_COLOR="1", GPA_PROGRESS="always")
        result = self.run_gpa()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("\x1b[K[FETCH  ] ~/repos/project (main)\n", result.stdout)
        self.assertIn("1 current", result.stdout)
        self.assertEqual(self.calls(), [])

    def git(self, *args):
        return subprocess.run(["git", *map(str, args)], env=self.env, text=True,
                              capture_output=True, check=True).stdout.strip()

    def test_local_fast_forward_and_skip(self):
        remote, seed = self.base / "remote.git", self.base / "seed"
        self.git("init", "--bare", remote); self.git("init", seed)
        self.git("-C", seed, "config", "user.email", "test@example.invalid")
        self.git("-C", seed, "config", "user.name", "Test")
        (seed / "file").write_text("first\n")
        self.git("-C", seed, "add", "file"); self.git("-C", seed, "commit", "-m", "first")
        self.git("-C", seed, "remote", "add", "origin", remote)
        self.git("-C", seed, "push", "origin", "HEAD")
        branch = self.git("-C", seed, "branch", "--show-current")
        self.git("-C", remote, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
        checkout = self.home / "repos/project"; checkout.parent.mkdir()
        self.git("clone", remote, checkout); self.git("init", self.home / ".skip")
        (seed / "file").write_text("second\n")
        self.git("-C", seed, "commit", "-am", "second"); self.git("-C", seed, "push", "origin", "HEAD")
        result = self.run_gpa("-v")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((checkout / "file").read_text(), "second\n")
        self.assertIn("1 updated", result.stdout); self.assertIn("1 skipped", result.stdout)

    def test_self_update_finishes_already_loaded_bash(self):
        checkout = self.home / "repos/git-pull-all"
        (checkout / ".git").mkdir(parents=True)
        executable = checkout / "bin/gpa"
        executable.parent.mkdir()
        executable.write_bytes((ROOT / "bin/gpa").read_bytes())
        self.env["UPDATE_TARGET"] = str(executable)
        git = self.mock_bin / "git"
        git.write_text(
            "#!/usr/bin/env python3\nimport os, pathlib, sys\n"
            "a=sys.argv[3:]; target=pathlib.Path(os.environ['UPDATE_TARGET'])\n"
            "if a == ['branch', '--show-current']: print('main')\n"
            "elif a == ['rev-parse', '--abbrev-ref', '@{upstream}']: print('origin/main')\n"
            "elif a == ['rev-parse', 'HEAD']: print('b' if 'new-version' in target.read_text() else 'a')\n"
            "elif a == ['pull', '--ff-only']:\n"
            " target.write_text('#!/usr/bin/env bash\\nprintf \\\"new-version\\\\n\\\"\\n')\n"
            " print('Updating a..b\\nFast-forward')\n"
            "else: sys.exit(2)\n")
        git.chmod(0o755)
        result = subprocess.run(["bash", str(executable)], env=self.env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 updated", result.stdout)
        self.assertNotIn("new-version", result.stdout)

    def install(self, *args):
        return subprocess.run(["bash", str(ROOT / "install.sh"),
                               "--no-dependencies", *args],
                              env=self.env, text=True, capture_output=True)

    def test_install_idempotent_and_preserves_conflict(self):
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.install().returncode, 0)
        link = self.home / ".local/bin/gpa"
        self.assertEqual(link.resolve(), ROOT / "bin/gpa")
        link.unlink(); link.write_text("keep\n")
        self.assertNotEqual(self.install("--migrate-dotfiles").returncode, 0)
        self.assertEqual(link.read_text(), "keep\n")

    def test_installed_symlink_finds_adjacent_checkout_dispatcher(self):
        self.hosts("linked\n")
        self.assertEqual(self.install().returncode, 0)
        result = subprocess.run([str(self.home / ".local/bin/gpa"), "-a"],
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0][3], "linked")

    def test_installer_creates_managed_textual_environment_idempotently(self):
        self.check_installer_platform("debian")

    def test_installer_provisions_ubuntu_venv(self):
        self.check_installer_platform("ubuntu")

    def test_installer_uses_termux_pkg_without_sudo_or_debian_packages(self):
        self.check_installer_platform("termux")

    def test_installer_rejects_unknown_distribution_even_with_apt(self):
        self.check_installer_platform("unknown")

    def test_installer_stops_after_package_failure(self):
        self.check_installer_platform("debian", package_failure=True)

    def test_installer_explains_missing_sudo_without_changing_installation(self):
        self.check_installer_platform("debian", missing_sudo=True)

    def check_installer_platform(self, platform, package_failure=False,
                                 missing_sudo=False):
        # Supply a fixture OS identity without introducing a production
        # environment override for a system-owned configuration file.
        os_release = self.base / "os-release"
        os_release.write_text(f"ID={platform}\n")
        installer = self.base / "install.sh"
        installer.write_text((ROOT / "install.sh").read_text().replace(
            "/etc/os-release", str(os_release)))
        if missing_sudo:
            # Simulate privilege discovery only in the fixture, independent of
            # the test runner's UID and installed sudo. No real package calls.
            installer.write_text(installer.read_text().replace(
                "(( EUID != 0 ))", "(( 1 ))").replace(
                "command -v sudo > /dev/null", "false"))
        self.env.pop("PREFIX", None)
        data_home = self.base / "data"
        install_log = self.base / "python-install.log"
        apt_log = self.base / "apt.log"
        apt_marker = self.base / "apt-installed"
        self.env.update(XDG_DATA_HOME=str(data_home), INSTALL_LOG=str(install_log),
                        APT_LOG=str(apt_log), APT_MARKER=str(apt_marker))
        apt = self.mock_bin / "apt-get"
        apt.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"$APT_LOG\"\n"
            "[[ \"${1:-}\" != install ]] || : > \"$APT_MARKER\"\n")
        apt.chmod(0o755)
        if package_failure:
            apt.write_text("#!/usr/bin/env bash\necho 'fixture package error' >&2\nexit 42\n")
        if platform == "termux":
            prefix = self.base / "termux"
            (prefix / "bin").mkdir(parents=True)
            self.env["PREFIX"] = str(prefix)
            marker = prefix / "bin/termux-info"
            marker.write_text("#!/usr/bin/env bash\nexit 0\n")
            marker.chmod(0o755)
            pkg = prefix / "bin/pkg"
            pkg.write_bytes(apt.read_bytes())
            pkg.chmod(0o755)
            # Any accidental apt/sudo call must fail, even though both exist.
            apt.write_text("#!/usr/bin/env bash\nexit 98\n")
            sudo = self.mock_bin / "sudo"
            sudo.write_text("#!/usr/bin/env bash\nexit 99\n")
            sudo.chmod(0o755)
        python = self.mock_bin / "python3"
        python.write_text(
            "#!/usr/bin/python3\n"
            "import os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:1] == ['-c'] and 'ensurepip' in args[1] and not pathlib.Path(os.environ['APT_MARKER']).exists(): sys.exit(1)\n"
            "if args[:2] == ['-m', 'venv']:\n"
            " root = pathlib.Path(args[-1]); (root / 'bin').mkdir(parents=True)\n"
            " managed = root / 'bin/python'\n"
            " managed.write_text('#!/usr/bin/python3\\nimport os, pathlib, sys\\nmarker=pathlib.Path(os.environ[\"INSTALL_LOG\"])\\nif sys.argv[1:2] == [\"-c\"]: sys.exit(0 if marker.exists() else 1)\\nif sys.argv[1:3] == [\"-m\", \"pip\"]: marker.write_text(\"installed\\\\n\")\\n')\n"
            " managed.chmod(0o755)\n"
            "sys.exit(0)\n")
        python.chmod(0o755)
        if platform == "unknown" or package_failure or missing_sudo:
            result = subprocess.run(["bash", str(installer)], env=self.env,
                                    text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            if platform == "unknown":
                self.assertIn("Debian, Ubuntu, and Termux only", result.stderr)
            if package_failure:
                self.assertEqual(result.returncode, 42)
                self.assertIn("fixture package error", result.stderr)
                self.assertIn("dependency setup failed while running:", result.stderr)
                self.assertIn("apt-get update", result.stderr)
                self.assertIn("installation stopped", result.stderr)
            if missing_sudo:
                self.assertEqual(result.returncode, 1)
                self.assertIn("missing system packages: python3-venv", result.stderr)
                self.assertIn("running as non-root and sudo was not found", result.stderr)
                self.assertIn("require root privileges", result.stderr)
                self.assertIn("  apt-get update\n  apt-get install -y -- python3-venv\n", result.stderr)
                self.assertIn("rerun ./install.sh as this user", result.stderr)
                self.assertFalse(apt_log.exists())
            self.assertFalse(apt_marker.exists())
            self.assertFalse((data_home / "gpa/venv").exists())
            self.assertFalse((self.home / ".local/bin/gpa").exists())
            return
        for _ in range(2):
            result = subprocess.run(["bash", str(installer)],
                                    env=self.env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(install_log.read_text(), "installed\n")
        expected = (["install -y python-pip python-ensurepip-wheels"]
                    if platform == "termux"
                    else ["update", "install -y -- python3-venv"])
        self.assertEqual(apt_log.read_text().splitlines(), expected)
        self.assertTrue((data_home / "gpa/venv/bin/python").exists())

    def test_remote_dispatch_prefers_installer_managed_python(self):
        self.hosts("managed\n")
        data_home = self.base / "data"
        managed = data_home / "gpa/venv/bin/python"
        managed.parent.mkdir(parents=True)
        invocation = self.base / "managed-python-used"
        self.env.update(XDG_DATA_HOME=str(data_home),
                        MANAGED_PYTHON_LOG=str(invocation))
        managed.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'used\\n' >> \"$MANAGED_PYTHON_LOG\"\n"
            "exec /usr/bin/python3 \"$@\"\n")
        managed.chmod(0o755)
        result = self.run_gpa("-a")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(invocation.read_text(), "used\nused\n")


if __name__ == "__main__":
    unittest.main()
