# PRD: concurrent hosts with a live grouped report

Status: basically usable on `feat/parallel-pull`; follow-up UAC work is tracked
below.
Branch: `feat/parallel-pull`.

This PRD replaces the earlier proposal for a mixed, host-prefixed terminal
log. The user accepted the grouped live report direction. Requirements below
capture that direction and the subsequently accepted behavior decisions.
The engineering details record the implemented choices. Visual refinement
will be assessed at final user acceptance (UAC), with no intermediate prototype
approval gate.

## Problem and intended outcome

Today `gpa -a` runs one SSH host at a time. Its grouped output makes each
host's repositories and summary easy to review, but a slow host delays all
later hosts. Simply interleaving concurrent output loses that useful grouping.

Run all configured hosts concurrently while retaining a report organized by
host. Each host's section grows as its output arrives. Users can watch work
in progress and review repository results with their corresponding summary.
Waiting for a host to finish before displaying its collected output is not
acceptable.

## Goals

- Launch every configured host without waiting for another host to finish.
- Display incoming output live inside its owning host's section.
- Keep each host's repository results, diagnostics, and summary together.
- Support reports longer than the terminal and preserve readable final output.
- Keep existing local pulls, configuration validation, and failure aggregation.

## Scope and non-goals

The feature changes remote orchestration and presentation for `gpa -a`,
including `-av`, `-va`, and long-form equivalents. Repository pulls within
one host remain sequential. There is no additional local update.

Out of scope: parallel pulls within a host, changing discovery or pull policy,
cloning repositories, remote installation, automatic retries, host deployment,
raw Git transfer progress, estimated completion percentages, and a general
purpose SSH management interface. Collapsing sections, filtering, and a host
navigation sidebar are not required for the first version.

## User experience

### Layout

Use a terminal application with a fixed overall status header and a vertically
scrollable report. Host sections appear in configuration order and never
reorder on completion or failure. Sections grow to fit their content; the
whole report scrolls rather than giving every host a small independent log
window. Repo results remain visible after completion.

Each section contains:

1. The configured SSH destination and host lifecycle status.
2. Repository results and the current FETCH indicator, when available.
3. Verbose details and diagnostics in their original local context.
4. The remote summary when received, followed by failure information if needed.

The display must distinguish host lifecycle from repository result labels.
A repository FAILED result can occur while the host is still running.
Host completion depends on the SSH/remote command's exit status, not parsed
summary text. Warnings and skips do not turn a successful host into a failure.

Suggested lifecycle labels are `connecting`, `running`, `completed`, and
`failed`. Show `running` only when there is evidence of remote execution;
launching SSH or receiving an SSH diagnostic is not sufficient evidence.
If that distinction is unavailable, use the honest broader label `starting`
or `active` rather than claiming a connection succeeded.

### Storyboard

These are illustrative frames, not output from the current command. Exact
borders, spacing, and wording can be refined during implementation.

Frame 1: all sections exist and connections start concurrently.

```text
gpa — 3 hosts active
────────────────────────────────────────────────────────
 HOST alpha                                  starting
────────────────────────────────────────────────────────
 HOST beta                                   starting
────────────────────────────────────────────────────────
 HOST gamma                                  starting
────────────────────────────────────────────────────────
```

Frame 2: alpha and beta independently add repository output. Gamma's failure
appears immediately in its own section while the other hosts continue.

```text
gpa — 2 hosts active · 1 failed
────────────────────────────────────────────────────────
 HOST alpha                                  running
    [CURRENT] ~/repos/api        already up to date
    [FETCH  ] ~/repos/worker     main
────────────────────────────────────────────────────────
 HOST beta                                   running
    [UPDATED] ~/repos/site       fast-forward
    [FETCH  ] ~/repos/docs       main
────────────────────────────────────────────────────────
 HOST gamma                                  failed
    ssh: connection refused
    [FAILED ] ssh or remote gpa failed (exit 255)
────────────────────────────────────────────────────────
```

Frame 3: alpha replaces its pending FETCH with a result and starts another
repo. Beta finishes and receives its summary without moving out of order.

```text
gpa — 1 host active · 1 completed · 1 failed
────────────────────────────────────────────────────────
 HOST alpha                                  running
    [CURRENT] ~/repos/api        already up to date
    [UPDATED] ~/repos/worker     fast-forward
    [FETCH  ] ~/repos/tools      main
────────────────────────────────────────────────────────
 HOST beta                                   completed
    [UPDATED] ~/repos/site       fast-forward
    [CURRENT] ~/repos/docs       already up to date

    gpa summary: 2 repos
        1 updated | 1 current | 0 skipped
        0 failed  | 0 warnings
────────────────────────────────────────────────────────
 HOST gamma                                  failed
    ssh: connection refused
    [FAILED ] ssh or remote gpa failed (exit 255)
────────────────────────────────────────────────────────
```

Frame 4: after all processes finish and output drains, retain complete sections
and the aggregate footer. Leave the live screen automatically
and print one complete grouped report into normal terminal scrollback. No key
press is required to finish the command. This final report preserves results
already displayed live; it is not the first presentation of host output.

```text
[HOST   ] alpha  (1/3)
    ...all alpha results and summary...

[HOST   ] beta  (2/3)
    ...all beta results and summary...

[HOST   ] gamma  (3/3)
    ssh: connection refused
    [FAILED ] ssh or remote gpa failed (exit 255)

gpa hosts: 2/3 completed
    Needs attention: gamma
```

### Scrolling, resizing, and readability

- Support mouse wheel and keyboard scrolling, including Page Up/Page Down.
- Retain all received report content for review, including off-screen sections.
- Preserve the reader's logical position when earlier sections gain lines;
  maintaining only an absolute scroll offset is insufficient.
- Do not jump to a different host or force scrolling to the newest event.
- Resize and wrap long paths, hostnames, and diagnostics without losing text.
- Keep the overall status visible while scrolling. Counts distinguish active,
  successful, and failed hosts; do not infer repo totals or percentage progress.
- Use textual status labels even with color. NO_COLOR disables color, not the
  live layout or progress. Preserve existing GPA_COLOR policy where applicable.

### Live output and verbose mode

A complete incoming line or recognized progress record becomes eligible for
the next UI refresh without waiting for its host to exit. Refreshes may batch
small bursts to avoid excessive redraws, but must continue while hosts run.
Limit redraws to at most 10 per second initially. Ingestion remains independent
of redraw cadence: retain every event and render the latest accumulated state.

A pending FETCH is replaced only by its corresponding result within the same
host section. Never execute remote cursor-control sequences directly against
the shared terminal. On disconnect, retain the pending repo context and show
host failure; do not invent a successful repo result.

For `-av`, keep branch, result, and Git details under their owning repository
inside the same host section. Preserve blank lines and meaningful indentation.
Treat remote content as text, not UI markup or executable terminal commands.

Live output retains the current remote granularity: FETCH precedes a pull,
while Git diagnostics are captured per repository. Raw transfer output is
not streamed by this feature. Ordinary unterminated text may wait until EOF;
it must be flushed then. Older remote versions must still display ordinary
output even if they cannot supply pending FETCH records.

### Noninteractive output

When the live terminal UI cannot be used, stream plain lines with a host label
on every line, including verbose details, blank lines, and diagnostics. This
is the fallback for redirects and unsupported terminals; it must also be live.
Do not emit cursor controls. Preserve stdout/stderr routing in plain mode.

Redirected output omits FETCH by default, as today. GPA_PROGRESS=always emits
readable progress lines; GPA_PROGRESS=never suppresses FETCH indicators without
suppressing results or host lifecycle state. The plain stream ends with the
aggregate footer and does not replay every host's output a second time.

For the terminal UI, capture both streams into the relevant host section so
raw stderr cannot corrupt the display. Preserve stream identity internally.
Enable the live UI only when stdin, stdout, and stderr are all attached to a
usable terminal. Partial redirection selects plain mode. For the final retained
report, send repository results and summaries to stdout and SSH diagnostics to
stderr, retaining host attribution. Both streams appear in the grouped report
on an ordinary terminal; separate streams do not promise a global write order.

## Execution and compatibility requirements

- Validate the entire hosts file before contacting any destination.
- Launch all host entries concurrently without an implicit worker limit.
  File order controls presentation, not observed process scheduling.
- Keep host input out of shell source and retain fixed remote command dispatch.
- Preserve existing flags, hosts-file precedence, and local `gpa` behavior.
- Use noninteractive SSH for remote mode. Authentication or host verification
  requiring a prompt fails that host with actionable diagnostics; users prepare
  authentication and known hosts through ordinary SSH beforehand. Do not weaken
  host verification. This intentionally changes the current prompt behavior.
- Inherit timeout policy from SSH configuration; do not add automatic retries.
- Continue other hosts after SSH or remote command failure.
- Preserve exit 0 for all-success, 1 for any host failure, and 2 for invalid
  arguments or host configuration. Keep attention names in configuration order.
- Drain stdout and stderr concurrently; large diagnostics must not deadlock
  another stream. Preserve per-stream order without promising cross-stream order.
- Retain separate state for duplicate host entries, identified by list index.
  Existing acceptance of duplicate destinations is not silently changed.
- Emit a host's terminal status only after its process exits and output drains.
- On interruption or renderer failure, restore the terminal, terminate and reap
  owned local workers/readers, and clean up temporary resources. Never report
  unfinished hosts as successful or kill unrelated processes. Closing SSH does
  not guarantee cancellation or rollback of remote Git operations.
- Ctrl+C exits with status 130 and retains a partial report. Mark unfinished
  hosts interrupted, preserving already completed successes and failures.
  Plain mode retains its already-streamed output and adds interruption status
  without replaying the report.
- Load the implementation needed for this invocation before pulls can update
  its own checkout. Account for deferred Python imports if introducing a helper.

## Technical direction and dependencies

Use Python with Textual on the dispatching machine for concurrent SSH process
management and a single renderer owning the terminal. Preserve the remote
Bash executable for discovery and pulling. Remote hosts do not need Textual.
Local-only `gpa` must remain usable without Python or Textual. Remote hosts
continue using Bash for pulls and event emission; Textual is dispatcher-only.

This is a direction, not a mandated class layout. Textual provides scrollable
containers and live content widgets, but logical scroll anchoring, host state,
stream routing, and progress normalization remain application responsibilities.

Relevant official documentation:

- [Textual scrollable containers](https://textual.textualize.io/api/containers/)
- [Textual RichLog](https://textual.textualize.io/widgets/rich_log/)
- [Rich Live overflow limitations](https://rich.readthedocs.io/en/stable/live.html)

Rich Live alone was considered, but its documented overflow behavior makes it
less suitable for a complete report longer than the terminal. The earlier
mixed-log design remains useful only for the plain-output fallback.

### Structured events and remote compatibility

Use a versioned structured event format between a supporting remote gpa and
its dispatcher. Events identify the repository and describe progress, results,
verbose details, and summary data without parsing display wording. The exact
encoding and capability detection are implementation details.

Here, “supporting” and “older” refer to installed copies of this same gpa tool,
not Python or Textual versions. Updating the dispatcher's checkout does not
update the gpa executable installed on each SSH host. For example, alpha may
already emit events while beta still prints today's ordinary text.

Both hosts must run concurrently and appear in their own live sections.
For a remote without event support, append its ordinary output as it arrives;
precise repository-aware FETCH replacement is not guaranteed. Do not require
all hosts to upgrade together. Capability detection must not execute pulls
twice, and unrecognized text must not be invented into structured results.
Remote gpa invoked normally continues producing human-readable output.

### Dependency behavior

Document supported Python/Textual versions and installation explicitly. Do not
install packages automatically when running gpa. Missing remote-mode runtime
dependencies fail before any SSH launch with installation guidance. Do not
silently fall back to sequential execution. Local-only gpa remains independent
of these dependencies.

### Implemented engineering details

- `bin/gpa-hosts.py` lives beside the Bash executable; resolving the installed
  `gpa` symlink locates the helper without another installed link. Remote mode
  supports Python 3.10+ and pins Textual 8.2.8 in an installer-managed venv.
  Automatic package provisioning currently uses Debian package names whenever
  `apt-get` exists and has been verified only on Debian 13. Ubuntu and Termux
  behavior remains follow-up work rather than a portability claim.
- Supporting remotes emit JSON records after an ASCII record-separator and a
  `GPA1` protocol tag. Malformed records remain inert, readable text. A remote
  that ignores `GPA_EVENT_STREAM=1` is handled as an older plain-text peer.
- The scroll anchor records the top visible host plus its within-section offset
  before a refresh, then restores that logical position after layout. Local UAC
  found that this restoration currently prevents reliable interactive scrolling
  while updates continue; this is a known gap rather than accepted behavior.
- The first interface uses `starting`, `running`, `completed`, `failed`, and
  `interrupted` lifecycle wording. Refreshes are coalesced on a 100 ms timer.
- Startup, renderer, and dependency failures return nonzero. SIGINT terminates
  owned SSH process groups, reaps them, retains partial data, and returns 130.

The user will assess scrolling experience and visual density at final UAC.
Implement and test the specified behavior first; do not build multiple complete
versions or add a prototype approval gate. Internal simulations remain useful
for engineering validation.

## Readiness and follow-up workflow

Local UAC has established that the concurrent host feature is basically usable,
but it is not visually complete. The following items are the canonical backlog
for this branch; they must not depend on conversation history or an individual
maintainer's environment.

### Known gaps

1. **Terminal color integration:** the live Textual screen currently uses its
   default dark palette/background rather than following the terminal color
   scheme. A follow-up should use terminal-aware or transparent styling and
   verify light and dark terminals plus `NO_COLOR` behavior.
2. **Scrolling during live updates:** keyboard and mouse scrolling cannot
   reliably reach content outside the visible area while refreshes continue.
   The current logical-anchor restoration is a likely interaction point. A fix
   must distinguish deliberate user scrolling from layout compensation, retain
   the reader's host-relative position when earlier sections grow, and preserve
   the complete final scrollback report.
3. **Installer platform matrix:** automatic dependency provisioning is verified
   in an isolated Debian 13 environment. Ubuntu and Termux remain unverified.
   Their package names, Python/venv behavior, privilege model, network effects,
   and repeat-run behavior must be observed before claiming support. In
   particular, the presence of `apt-get` must not be treated as proof that
   Debian package names or `sudo` are appropriate on Termux.

### Follow-up checklist

- Reproduce UI issues with `examples/demo-output.sh` and isolated fixtures;
  never use live repository updates for visual testing.
- For each installation target, record the OS/release, available package
  manager and commands, required package names, Python version, venv behavior,
  privilege requirements, first-run effects, and idempotent rerun result.
- Run installation trials from a temporary checkout and isolated `HOME` /
  `XDG_DATA_HOME`. Use real SSH only when separately authorized, and never run
  `gpa` against user repositories as part of validation.
- Update `install.sh`, its frontmatter and diagnostics, regression tests,
  `README.md`, and this decision record together when adding platform support.
- Run the repository checks in this document and the Textual-enabled UI suite.
  Exercise overflow, resize, keyboard and mouse scrolling, active refreshes,
  light/dark terminal colors, and the retained final report.
- Return terminal density and scrolling behavior for final user acceptance only
  after the known gaps above are resolved; no intermediate prototype approval
  is required.

## Acceptance criteria

1. A barrier-based mock proves all hosts start while every host remains blocked;
   no completion is needed to launch the next host.
2. Output from at least two still-running hosts is visible in their own sections
   before either is released to finish. Test acknowledgements, not elapsed-time
   guesses, establish streaming.
3. Repository results, verbose details, diagnostics, and summaries remain under
   the correct host through interleaved arrivals and out-of-order completion.
4. FETCH replacement affects only the corresponding host/repo. Older output,
   EOF without newline, blank lines, long lines, and high-volume stderr work.
5. Reports several screens long retain all content. Scrolling up stays anchored
   while earlier hosts add output; resizing keeps all sections readable.
   **Open:** retained content and resize have automated coverage, but local UAC
   found live interactive scrolling unreliable, so this criterion is not yet
   fully met.
6. A failed connection and a remote pull failure do not stop a slow successful
   host. The final footer follows all drained output and has correct status.
7. Terminal completion leaves the full grouped report in normal scrollback;
   the run never withholds a host's output until its completion.
8. Plain mode streams attributed output, preserves stream routing, and honors
   progress/color policy without cursor movement or a duplicated final report.
9. Invalid later host entries cause no SSH launches. Existing options, duplicate
   entries, local behavior, and self-update handling have regression coverage.
10. Ctrl+C and renderer failure leave a usable terminal, no owned local workers
    or readers running, and no temporary resources behind. Ctrl+C returns 130
    and preserves a partial report with interrupted hosts distinguished.
11. The offline demo visibly exercises overlapping hosts, staggered repo results,
    an early failure, a slow host, and compact/verbose presentation. It never
    contacts real SSH hosts or updates user repositories.

12. Noninteractive SSH fails hosts requiring prompts without blocking other
    hosts; configured timeout policy remains effective and no retry is added.
13. Supporting and older remote gpa fixtures both stream live without duplicate
    pulls. Structured events associate results correctly; plain remote text
    remains readable in its host section.
14. Missing dispatcher dependencies cause no SSH launches and show installation
    guidance; local-only Bash usage remains functional. Partial redirection
    selects plain output.

## Delivery and validation

Implement the grouped UI and concurrent SSH with isolated simulated fixtures,
including overflow, scroll anchoring, resize, compatibility, and final scrollback
output. Present the completed feature for final UAC; visual refinements follow
that feedback rather than an intermediate prototype comparison. Update script
headers, nearby comments, README, host-file examples, and the offline demo to
match the implemented contract. Do not present this PRD as shipped behavior.

Run from the repository root before handing off changes:

```sh
bash -n bin/gpa install.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
git diff --check
```

Add meaningful concurrency, process-cleanup, plain-output, and terminal UI
coverage during implementation. Implementation and test work does not authorize
live SSH tests, deployment, or publishing. Record handoff state in the readiness
section above and in commit messages; do not rely on chat context for unfinished
acceptance work.
