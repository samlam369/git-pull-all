# git-pull-all

A small Bash command for pulling the Git repositories you keep on your machines.
Run `gpa` to update repositories directly under `~` and `~/repos`, or `gpa -a`
to run the same command concurrently on a list of SSH hosts.

I use this to keep my own checkouts up to date. The layout and defaults
reflect that workflow; there is no service to run or account to create.

Here's what `gpa -a` looks like (excerpt from the simulated demo):

```text
[HOST   ] server-ok  (1/4)
    [UPDATED] ~/repos/app               fast-forward          (main)
    [CURRENT] ~/repos/current           already up to date    (main)
    [SKIPPED] ~/repos/detached          no upstream           (detached@aaaaaaa)
    [SKIPPED] ~/repos/scratch           no upstream           (main)

    gpa summary: 4 repos
        1 updated | 1 current | 2 skipped
        0 failed  | 0 warnings
```

Run `bash examples/demo-output.sh` from this checkout to see the full demo,
including warnings, failed pulls, and an unreachable host. It uses simulated
Git and SSH results, without network activity or changes to your repositories.
Add `-v` to include branch and Git output details.

## What it does

- Finds direct child repositories under `~` and `~/repos`, including hidden
  directories, directory symlinks, and linked worktrees.
- Runs `git pull --ff-only` on the current branch when it has an upstream.
- Reports updated, unchanged, skipped, and failed repositories, then a summary.
- Optionally starts the operation concurrently on configured SSH hosts and
  presents their results in file order.

It does not search recursively, clone missing repositories, switch branches,
push commits, or deploy software to other hosts. The local search paths are
currently fixed; there is no repository exclusion option or dry-run mode.

## Install

The one-step installer checks Bash, Git, sed, SSH, GNU-compatible `realpath`,
Python 3.10+, and Python venv support. Its automatic system-package path is
currently verified on Debian 13: when `apt-get` is available, it installs
missing Debian package names, using `sudo` when required. This can prompt for
privilege escalation, uses the package network, and is not rolled back if a
later step fails.

It then creates `${XDG_DATA_HOME:-$HOME/.local/share}/gpa/venv` and installs the
supported Textual 8.2.8 release there. The environment is owned by this tool;
it avoids modifying system Python or relying on an activated shell environment.
Reruns reuse it when the requested version is already installed.

Ubuntu and Termux package setup still need environment-specific validation;
having an `apt-get` command alone does not guarantee Debian package names or
privilege handling are correct there. Until those paths are completed, install
the listed commands and Python venv/ensurepip support using the platform's
package manager before running the normal installer; it can then create the
managed Textual environment without invoking `apt-get`. Alternatively, fully
provision Python and Textual 8.2.8 externally and use
`install.sh --no-dependencies`. Other Linux distributions must use one of these
paths. macOS/BSD setup is not verified and may need GNU coreutils. The flag
skips all dependency checks, package operations, and managed-venv setup, so the
caller owns those dependencies. Local-only execution itself remains independent
of Python and Textual. Remote hosts need Bash, Git, sed, and `env` to receive
pulls.

```sh
mkdir -p ~/repos
git clone https://github.com/samlam369/git-pull-all.git ~/repos/git-pull-all
~/repos/git-pull-all/install.sh
```

Ensure `~/.local/bin` is on PATH. The installer creates a symlink to `bin/gpa`
in this checkout and does not change shell startup files or host configuration.
Rerunning it is safe; it refuses to replace unrelated files or symlinks.

## Local use

```sh
gpa       # compact results and summary
gpa -v    # include branch and Git output details
gpa --help
```

For example, `~/project` and `~/repos/app` are included if they are Git
repositories; `~/repos/work/app` is not. Your current directory does not affect
discovery. A repository reached through multiple symlinks can be processed
more than once.

Example summary (illustrative):

```text
gpa summary: 4 repos
    1 updated | 2 current | 1 skipped
    0 failed  | 0 warnings
```

Branches without an upstream, including detached HEADs, are skipped. A failed
pull does not stop later repositories. `--verbose` is equivalent to `-v`.
Summary colors are automatic on a color-capable terminal. `GPA_COLOR=always`
forces them, `GPA_COLOR=never` disables them, and other values use automatic
detection. Nonempty `NO_COLOR` takes precedence and disables colors.

On a terminal, `[FETCH]` identifies the repository being pulled immediately,
then the completed result replaces that line. This works in local and `-a`
mode, including verbose output. `NO_COLOR` does not disable progress.
`GPA_PROGRESS=never` disables it; `GPA_PROGRESS=always` forces it (nonterminal
output then includes newline-delimited progress control records). Other values
use automatic terminal detection. Redirected output omits progress by default.

## SSH hosts

Install `gpa` on each remote machine first and make it available on that user's
noninteractive SSH PATH. Check this with `ssh server-one 'command -v gpa'`.
Authentication and SSH aliases use your existing SSH configuration.

Create `${XDG_CONFIG_HOME:-$HOME/.config}/gpa/hosts`, for example:

```text
# One SSH destination per line, in presentation order.
server-one
user@server-two.example
```

Blank lines and full-line comments are ignored. Each destination must occupy
a whole line without surrounding whitespace. Destinations start with an ASCII
letter, digit, or underscore and otherwise contain only letters, digits,
underscores, dots, `@`, colons, or hyphens. Put ports and SSH options in
`~/.ssh/config`; use aliases for addresses outside this format.
Missing, empty, or invalid configuration fails before any host is contacted.
The installer does not create or overwrite this file; `examples/hosts` is a
template for your own configuration.

```sh
gpa -a                    # configured hosts, compact output
gpa -av                   # verbose on every host
gpa -va                   # same as -av
gpa --all --verbose       # same as -av
GPA_HOSTS_FILE=/path/to/hosts gpa -a
```

Remote mode validates the complete file, then launches every host concurrently
with noninteractive SSH. On a terminal, Textual keeps an overall status line
visible above one report. Host sections stay in file order while they grow
live, and results do not reorder when a host finishes. When all output is
drained, the interface closes automatically and prints the complete grouped
report into normal terminal scrollback.

The first usable live interface has two known visual limitations. It currently
uses Textual's dark palette instead of following the terminal's color scheme,
and scrolling beyond the visible area is not reliable while refreshes are
active. The complete report remains available in normal terminal scrollback
after the run. Terminal-aware styling and live keyboard/mouse scrolling are
tracked as follow-up UAC work in `docs/parallel-hosts.md`.

Each host has a separate lifecycle (`starting`, `running`, `completed`,
`failed`, or `interrupted`). A failed repository does not finish its host; the
SSH/remote command exit status does. A disconnect retains any pending FETCH and
adds the host failure. It attempts every host even after a failure. There is no
separate local update unless this machine is also configured as a destination.

Preview updated, current, skipped, warning, and failed results without network
activity. The demo includes a mixed-result host, an SSH failure, and an empty
host processed after the failures:

```sh
bash examples/demo-output.sh
bash examples/demo-output.sh -v
```

When any standard stream is redirected, the plain fallback streams each line
immediately with `[destination]` attribution. Blank lines, verbose details, and
SSH diagnostics are attributed too; diagnostics remain on stderr. It emits no
cursor controls and does not replay host output at completion. Output uses four
spaces per indentation level in the final terminal report, including local
summaries and verbose details.

The final footer reports host command completion and names hosts to revisit:

```text
gpa hosts: 2/4 completed
    Needs attention: server-mixed, server-offline
```

Completed means the remote command exited successfully, including repository
skips and warnings. The attention list covers nonzero SSH/remote command exits;
those hosts may still have successful pulls. Repository counts and warnings
stay in each host's summary. When every host completes, only the first line
is printed.

An updated remote `gpa` sends versioned records so FETCH can be replaced only by
the matching repository result inside that host. Older remote versions still
stream ordinary text immediately; they may not provide replaceable FETCH
records. Capability negotiation never runs a pull twice. Progress is forwarded
without allocating an SSH terminal. Redirects omit FETCH by default;
`GPA_PROGRESS=always` makes it a readable attributed line and
`GPA_PROGRESS=never` suppresses it.

Remote summary colors follow the caller's color choice through `GPA_COLOR`,
without allocating an SSH terminal. Failed repository counts and the attention
list are red; warning counts are yellow. Redirected output is plain by default.
Nonempty `NO_COLOR` on the caller disables colors throughout; a remote's own
`NO_COLOR` can also disable its colors. Remote hosts need the updated `gpa` to
honor the forwarded color choice.

SSH uses normal host verification but enables `BatchMode=yes`: authentication
or verification that needs a prompt fails that host instead of blocking the
report. There is no built-in timeout or retry. Configure timeout policy and
prepare keys and known hosts through ordinary SSH configuration.

Ctrl+C marks unfinished hosts interrupted, retains received output, terminates
and reaps dispatcher-owned SSH processes, and exits 130. Closing local SSH does
not guarantee cancellation or rollback of a remote Git operation.

## Effects and trust

This command updates working trees. `--ff-only` rejects divergent histories,
but it is not a preview or a backup. Existing Git configuration still applies,
including credential helpers, hooks, filters, and autostash settings. Use it
with repositories and configuration you trust, as you would a manual pull.

Git terminal prompts are disabled with `GIT_TERMINAL_PROMPT=0`; that does not
make SSH or every credential helper noninteractive. The tool has no telemetry
or hosted backend. Network activity is through Git, SSH, and their configured
helpers. Output can contain repository paths, remote URLs, branch names, and
remote usernames/hostnames; review it before sharing logs.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success, including skipped repositories |
| `1` | At least one pull, SSH connection, or remote command failed |
| `2` | Invalid arguments or local remote-host configuration |

A remote command's nonzero exit is summarized as `1` by the calling `gpa -a`.

## Update or uninstall

Pull this checkout to update the installed command. No reinstall or Stow
operation is required. Because it lives under `~/repos`, `gpa` can update its
own checkout too. The implementation is parsed into a Bash function before
pulls start, so the current invocation finishes using the code it started with;
new code takes effect on the next invocation. Updates are not pinned or
signature-verified by this tool.

To uninstall, remove `~/.local/bin/gpa` after checking that it is the symlink
pointing at this checkout. The managed environment under
`${XDG_DATA_HOME:-$HOME/.local/share}/gpa/venv`, the checkout, and host
configuration are separate paths and can then be removed if no longer needed.
System packages installed through apt are not removed automatically because
they may be shared with other programs.

## Development

The defaults reflect my current workflow and can be revisited as needs change.
Comments record intent, assumptions, and useful tradeoffs so future changes can
be made with context; they do not imply every existing choice is essential or
permanent. When changing behavior, update its documentation and tests too.

```sh
bash -n bin/gpa install.sh
python3 -m py_compile bin/gpa-hosts.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
git diff --check
```

Tests use temporary homes, local Git fixtures, and mocked SSH. They do not
update user repositories or contact configured hosts. Bug reports with a small
reproduction are welcome; remove personal details from logs first.

## License

[MIT](LICENSE).
