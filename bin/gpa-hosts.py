#!/usr/bin/env python3
"""Run gpa concurrently on configured SSH destinations and group its output.

This dispatcher is invoked by ``bin/gpa -a`` after Bash has parsed and
validated the complete hosts file.  It starts one noninteractive SSH process
per list entry, drains both pipes independently, and retains every record in
configuration order.  A Textual interface is used only when all three standard
streams are terminals; redirects receive a live, host-attributed text stream.
SSH stdin is disconnected: only the renderer may consume terminal input.

Inputs are host arguments plus ``--verbose`` and the caller's colour/progress
policies.  SSH configuration owns authentication, verification, and timeouts;
the fixed remote command enables gpa's versioned event stream without placing
host data in shell source.  Older remote copies simply produce ordinary text.

The program changes no local repositories or configuration, but remote gpa may
update working trees.  Failures are aggregated after every host is drained.
SIGINT terminates and reaps only subprocess groups created here, prints the
partial report in terminal mode, and exits 130.  Normal success/failure uses
0/1; dispatcher or renderer failures use 1 and diagnostics go to stderr.

Requires Python 3.10+.  Live mode additionally requires Textual 8.x;
plain mode deliberately has no third-party dependency.  This file lives beside
``bin/gpa`` so the installed symlink continues to find the matching dispatcher.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import json
import os
import re
import signal
import sys
import time
from typing import Callable, Literal


EVENT_PREFIX = "\x1eGPA1 "
ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
Stream = Literal["stdout", "stderr"]


def clean_text(value: str) -> str:
    """Make remote output inert text while retaining tabs and readable data."""
    value = value.replace("\r", "")
    value = ANSI_RE.sub("", value)
    return "".join(character if character in "\t" or ord(character) >= 32 else "�"
                   for character in value)


def decorate_ansi(value: str, color: bool) -> str:
    """Apply local semantic colours after untrusted remote controls are removed."""
    if not color:
        return value
    if "[FAILED" in value or re.search(r"\b[1-9][0-9]* failed\b", value):
        return f"\x1b[31m{value}\x1b[0m"
    if re.search(r"\b[1-9][0-9]* warnings?\b", value):
        return f"\x1b[33m{value}\x1b[0m"
    return value


def logical_scroll_anchor(scroll_y: float,
                          regions: list[tuple[str, float, float]]) -> tuple[str, float] | None:
    """Identify the visible host and offset, independent of absolute layout."""
    for identity, top, bottom in regions:
        if top <= scroll_y < bottom:
            return identity, scroll_y - top
    return None


def restore_scroll_y(anchor: tuple[str, float] | None,
                     tops: dict[str, float], fallback: float) -> float:
    if anchor is None or anchor[0] not in tops:
        return fallback
    return tops[anchor[0]] + anchor[1]


@dataclass
class Record:
    stream: Stream
    text: str
    repo: str | None = None
    pending: bool = False


@dataclass
class HostState:
    index: int
    destination: str
    lifecycle: str = "starting"
    records: list[Record] = field(default_factory=list)
    pending: dict[str, Record] = field(default_factory=dict)
    process: asyncio.subprocess.Process | None = None
    status: int | None = None

    def ingest(self, stream: Stream, raw: str) -> Record | None:
        """Normalize one complete remote record without interpreting old prose."""
        if stream == "stdout" and raw.startswith(EVENT_PREFIX):
            try:
                event = json.loads(raw[len(EVENT_PREFIX):])
                if (not isinstance(event, dict) or event.get("v") != 1 or
                        event.get("type") not in {"fetch", "result", "detail", "summary"} or
                        not isinstance(event.get("text", ""), str)):
                    raise ValueError
                text = clean_text(event.get("text", ""))
                repo = event.get("repo")
                if repo is not None and not isinstance(repo, str):
                    raise ValueError
                if event["type"] in {"fetch", "result"} and not repo:
                    raise ValueError
            except (json.JSONDecodeError, TypeError, ValueError):
                record = Record(stream, clean_text(raw))
                self.records.append(record)
                return record
            self.lifecycle = "running"
            if event["type"] == "fetch" and repo:
                old = self.pending.pop(repo, None)
                if old is not None:
                    old.pending = False
                record = Record(stream, text, repo, True)
                self.records.append(record)
                self.pending[repo] = record
                return record
            if event["type"] == "result" and repo:
                old = self.pending.pop(repo, None)
                if old is not None:
                    try:
                        self.records.remove(old)
                    except ValueError:
                        pass
            record = Record(stream, text, repo)
            self.records.append(record)
            return record

        text = clean_text(raw)
        if stream == "stdout":
            self.lifecycle = "running"
        record = Record(stream, text)
        self.records.append(record)
        return record


Update = Callable[[HostState, Record | None], None]


class Dispatcher:
    """Own concurrent SSH children, stream readers, and interruption cleanup."""

    def __init__(self, hosts: list[str], remote_command: str, update: Update) -> None:
        self.hosts = [HostState(index, host) for index, host in enumerate(hosts)]
        self.remote_command = remote_command
        self.update = update
        self.interrupted = False

    async def _read(self, host: HostState, stream: Stream,
                    reader: asyncio.StreamReader) -> None:
        buffered = bytearray()
        while True:
            data = await reader.read(65536)
            if not data:
                if buffered:
                    text = buffered.decode("utf-8", errors="replace")
                    self.update(host, host.ingest(stream, text))
                return
            buffered.extend(data)
            while True:
                try:
                    boundary = buffered.index(b"\n")
                except ValueError:
                    break
                line = bytes(buffered[:boundary])
                del buffered[:boundary + 1]
                self.update(host, host.ingest(
                    stream, line.decode("utf-8", errors="replace")))

    async def _run_host(self, host: HostState) -> None:
        try:
            host.process = await asyncio.create_subprocess_exec(
                "ssh", "-q", "-o", "BatchMode=yes", host.destination,
                self.remote_command,
                # BatchMode disables prompts, not stdin forwarding. Inheriting
                # the terminal lets SSH steal keys and mouse escape sequences
                # from Textual; remote gpa has no interactive input contract.
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            assert host.process.stdout is not None and host.process.stderr is not None
            await asyncio.gather(
                self._read(host, "stdout", host.process.stdout),
                self._read(host, "stderr", host.process.stderr),
            )
            host.status = await host.process.wait()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # spawn failures are host failures, not crashes
            self.update(host, host.ingest("stderr", f"gpa: could not start ssh: {error}"))
            host.status = 127
        if host.status == 0:
            host.lifecycle = "completed"
        elif self.interrupted:
            host.status = 130
            host.lifecycle = "interrupted"
            if not any(record.text == "[INTERRUPTED] host did not finish"
                       for record in host.records):
                interruption = Record("stdout", "[INTERRUPTED] host did not finish")
                host.records.append(interruption)
                self.update(host, interruption)
        else:
            host.lifecycle = "failed"
            failure = Record(
                "stdout",
                f"[FAILED ] {host.destination}  ssh or remote gpa failed "
                f"(exit {host.status})",
            )
            host.records.append(failure)
            self.update(host, failure)
        self.update(host, None)

    async def run(self) -> None:
        # Creating every task before awaiting any one host is the launch
        # barrier: presentation order never becomes a worker limit.
        tasks = [asyncio.create_task(self._run_host(host)) for host in self.hosts]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            await self.interrupt()
            raise

    async def interrupt(self) -> None:
        self.interrupted = True
        running = [host for host in self.hosts
                   if host.process is not None and host.process.returncode is None]
        for host in running:
            try:
                os.killpg(host.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if running:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(host.process.wait() for host in running)), 2.0)
            except asyncio.TimeoutError:
                for host in running:
                    if host.process.returncode is None:
                        try:
                            os.killpg(host.process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                await asyncio.gather(*(host.process.wait() for host in running))
        for host in self.hosts:
            if host.status is None:
                host.status = 130
                host.lifecycle = "interrupted"
                if not any(record.text == "[INTERRUPTED] host did not finish"
                           for record in host.records):
                    interruption = Record("stdout", "[INTERRUPTED] host did not finish")
                    host.records.append(interruption)
                    self.update(host, interruption)
                self.update(host, None)


def footer(hosts: list[HostState], color: bool = False) -> list[str]:
    completed = sum(host.status == 0 for host in hosts)
    lines = [f"gpa hosts: {completed}/{len(hosts)} completed"]
    attention = [host.destination for host in hosts if host.status not in (None, 0)]
    if attention:
        value = f"Needs attention: {', '.join(attention)}"
        if color:
            value = f"\x1b[31m{value}\x1b[0m"
        lines.append(f"    {value}")
    return lines


def host_heading(host: HostState, total: int, *, live: bool = False) -> str:
    """Keep host identity/order identical; lifecycle is live-view context."""
    heading = f"[HOST   ] {host.destination}  ({host.index + 1}/{total})"
    return f"{heading}  {host.lifecycle}" if live else heading


def print_final(hosts: list[HostState], color: bool = False) -> None:
    """Print a retained grouped report, routing each captured record by origin."""
    for position, host in enumerate(hosts, 1):
        if position > 1:
            print()
        print(host_heading(host, len(hosts)))
        for record in host.records:
            target = sys.stdout if record.stream == "stdout" else sys.stderr
            if record.stream == "stderr":
                line = (f"[{host.destination}]     {record.text}"
                        if record.text else f"[{host.destination}]")
            else:
                line = f"    {record.text}" if record.text else ""
            print(decorate_ansi(line, color), file=target)
    print()
    for line in footer(hosts, color):
        print(line)


async def run_plain(hosts: list[str], command: str,
                    color: bool = False) -> tuple[list[HostState], bool]:
    lock = asyncio.Lock()
    writes: list[asyncio.Task[None]] = []

    def update(host: HostState, record: Record | None) -> None:
        async def write() -> None:
            if record is None:
                state = f"[HOST {host.lifecycle.upper()}]"
                if color and host.lifecycle == "failed":
                    state = f"\x1b[31m{state}\x1b[0m"
                print(f"[{host.destination}] {state}", flush=True)
            elif not record.pending or os.environ.get("GPA_PROGRESS") == "always":
                target = sys.stdout if record.stream == "stdout" else sys.stderr
                line = f"[{host.destination}] {record.text}"
                print(decorate_ansi(line, color), file=target, flush=True)
        async def locked_write() -> None:
            async with lock:
                await write()
        writes.append(asyncio.create_task(locked_write()))

    dispatcher = Dispatcher(hosts, command, update)
    for host in dispatcher.hosts:
        print(f"[{host.destination}] [HOST STARTING]", flush=True)
    try:
        await dispatcher.run()
    except asyncio.CancelledError:
        await dispatcher.interrupt()
    if writes:
        await asyncio.gather(*writes)
    print()
    for line in footer(dispatcher.hosts, color):
        print(line)
    return dispatcher.hosts, dispatcher.interrupted


def textual_version_supported() -> tuple[bool, str]:
    try:
        from importlib.metadata import PackageNotFoundError, version
        value = version("textual")
    except PackageNotFoundError:
        return False, "Textual is not installed"
    try:
        major = int(value.split(".", 1)[0])
    except ValueError:
        return False, f"unrecognized Textual version {value}"
    return major == 8, value


def run_live(hosts: list[str], command: str, color: bool) -> tuple[list[HostState], bool]:
    """Import the optional renderer only after dependency validation."""
    from textual.app import App, ComposeResult
    from textual.containers import VerticalScroll
    from textual.widgets import Static
    from rich.text import Text

    class HostReport(Static):
        pass

    class ReportApp(App[None]):
        def get_driver_class(self):
            """Use cell mouse coordinates and SIGWINCH on the POSIX driver."""
            default = super().get_driver_class()
            if os.name != "posix":
                return default
            from textual.drivers.linux_driver import LinuxDriver
            if default is not LinuxDriver:
                return default

            class CellMouseDriver(LinuxDriver):
                def _query_in_band_window_resize(self) -> None:
                    # Textual 8 couples DEC 2048 to pixel mouse mode 1016.
                    # T3 0.0.40 advertises 2048 but omits the initial size
                    # report: clicks then decode outside the screen and clear
                    # keyboard focus. Keep standard cell-based SGR mouse and
                    # SIGWINCH instead. Recheck this private hook on upgrades.
                    pass

            return CellMouseDriver

        # Native ANSI defaults let the terminal own foreground/background,
        # including light palettes and transparency. RGB theme backgrounds
        # can contrast with terminal-painted gaps between character rows.
        CSS = """
        Screen { layout: vertical; }
        #status { dock: top; height: 1; padding: 0 1; text-style: bold; }
        #report {
            height: 1fr;
            scrollbar-gutter: stable;
            scrollbar-color: ansi_default;
            scrollbar-color-hover: ansi_default;
            scrollbar-color-active: ansi_default;
        }
        HostReport { height: auto; padding: 0 1 1 1; }
        """
        BINDINGS = [("ctrl+c", "interrupt", "Interrupt")]

        def __init__(self) -> None:
            super().__init__(ansi_color=True)
            self.dispatcher = Dispatcher(hosts, command, self.receive)
            self.dirty: set[int] = set(range(len(hosts)))
            self.runner: asyncio.Task[None] | None = None
            self.was_interrupted = False

        def compose(self) -> ComposeResult:
            yield Static("", id="status", markup=False)
            # Native ANSI supplies the terminal background for the track; CSS
            # above replaces its blue thumb with the default text color in
            # every interaction state, following both light and dark terminals.
            with VerticalScroll(id="report", classes="-ansi-scrollbar"):
                for host in self.dispatcher.hosts:
                    yield HostReport("", id=f"host-{host.index}", markup=False)

        def on_mount(self) -> None:
            self.query_one("#report", VerticalScroll).focus()
            self.set_interval(0.1, self.redraw_dirty)
            self.runner = asyncio.create_task(self.run_dispatcher())

        async def run_dispatcher(self) -> None:
            try:
                await self.dispatcher.run()
                self.redraw_dirty()
                self.set_timer(0.12, self.exit)
            except asyncio.CancelledError:
                return
            except Exception as error:
                self.dispatcher.interrupted = True
                await self.dispatcher.interrupt()
                self.dispatcher.hosts[0].records.append(
                    Record("stderr", f"gpa: renderer worker failed: {error}"))
                self.exit()

        def receive(self, host: HostState, record: Record | None) -> None:
            self.dirty.add(host.index)

        def redraw_dirty(self) -> None:
            # An idle refresh must not cancel Textual's scroll animation.
            if not self.dirty:
                return
            report = self.query_one("#report", VerticalScroll)
            scroll_before = (report.scroll_y, report.scroll_target_y)
            widgets = [self.query_one(f"#host-{host.index}", HostReport)
                       for host in self.dispatcher.hosts]
            anchor = logical_scroll_anchor(
                report.scroll_y,
                [(widget.id, widget.virtual_region.y, widget.virtual_region.bottom)
                 for widget in widgets],
            )
            for index in sorted(self.dirty):
                host = self.dispatcher.hosts[index]
                body = [host_heading(host, len(self.dispatcher.hosts), live=True)]
                body.extend(f"    {record.text}" if record.text else ""
                            for record in host.records)
                content = Text("\n".join(body))
                content.highlight_regex(r"(?m)^\[HOST   \][^\n]*", "bold")
                if color:
                    content.highlight_regex(
                        r"(?m)(^\[HOST   \][^\n]*\s(?:failed|interrupted)$|"
                        r"^\s*\[FAILED\s*\][^\n]*|\b[1-9][0-9]* failed\b)",
                        "red",
                    )
                    content.highlight_regex(r"\b[1-9][0-9]* warnings?\b", "yellow")
                self.query_one(f"#host-{index}", HostReport).update(content)
            self.dirty.clear()
            active = sum(host.status is None for host in self.dispatcher.hosts)
            done = sum(host.status == 0 for host in self.dispatcher.hosts)
            failed = sum(host.status not in (None, 0) for host in self.dispatcher.hosts)
            parts = [f"gpa — {active} {'host' if active == 1 else 'hosts'} active"]
            if done:
                parts.append(f"{done} completed")
            if failed:
                parts.append(f"{failed} failed")
            self.query_one("#status", Static).update(" · ".join(parts))
            if anchor is not None:
                anchor_id, offset = anchor
                def restore() -> None:
                    # User input/animation since the snapshot takes precedence.
                    # A stale anchor must never rewind a deliberate scroll.
                    if (report.scroll_y, report.scroll_target_y) != scroll_before:
                        return
                    if report.scroll_y != report.scroll_target_y:
                        return
                    tops = {widget.id: widget.virtual_region.y for widget in widgets}
                    restored = restore_scroll_y((anchor_id, offset), tops, report.scroll_y)
                    # scroll_to stops an active animation even when its value
                    # is unchanged. Compensate only for actual layout movement,
                    # immediately after layout rather than another refresh later.
                    if restored != report.scroll_y:
                        report.scroll_to(y=restored, animate=False, immediate=True)
                self.call_after_refresh(restore)

        async def action_interrupt(self) -> None:
            self.was_interrupted = True
            await self.dispatcher.interrupt()
            if self.runner is not None:
                self.runner.cancel()
            self.redraw_dirty()
            self.exit()

    app = ReportApp()
    try:
        app.run(mouse=True)
    except BaseException:
        # A renderer exception may close Textual's event loop before its async
        # worker can clean up. Fall back to process-group signals and waitpid so
        # no dispatcher-owned SSH process survives a broken terminal session.
        for host in app.dispatcher.hosts:
            process = host.process
            if process is not None and process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    deadline = time.monotonic() + 2.0
                    while time.monotonic() < deadline:
                        waited, _ = os.waitpid(process.pid, os.WNOHANG)
                        if waited:
                            break
                        time.sleep(0.02)
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                        os.waitpid(process.pid, 0)
                except ChildProcessError:
                    pass
                except ProcessLookupError:
                    pass
        raise
    return app.dispatcher.hosts, app.was_interrupted or app.dispatcher.interrupted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--color", choices=("always", "never"), required=True)
    parser.add_argument("--progress", choices=("always", "never"), required=True)
    parser.add_argument("hosts", nargs="+")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    remote = (f"env GPA_COLOR={args.color} GPA_PROGRESS={args.progress} "
              "GPA_EVENT_STREAM=1 gpa" + (" -v" if args.verbose else ""))
    live = all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr))
    if live:
        supported, detail = textual_version_supported()
        if not supported:
            print("gpa: live remote mode requires Textual >=8,<9; "
                  f"{detail}. Rerun install.sh to create the managed environment",
                  file=sys.stderr)
            return 1
        try:
            states, interrupted = run_live(args.hosts, remote, args.color == "always")
        except Exception as error:
            print(f"gpa: live renderer failed: {error}", file=sys.stderr)
            return 1
        print_final(states, args.color == "always")
    else:
        try:
            states, interrupted = asyncio.run(
                run_plain(args.hosts, remote, args.color == "always"))
        except KeyboardInterrupt:
            # asyncio has already cancelled the dispatcher and reaped children.
            return 130
    if interrupted:
        return 130
    return 0 if all(host.status == 0 for host in states) else 1


if __name__ == "__main__":
    raise SystemExit(main())
