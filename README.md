# git-pull-all

A small Bash command for pulling the Git repositories you keep on your machines.
Run `gpa` to update repositories directly under `~` and `~/repos`, or `gpa -a`
to run the same command on a list of SSH hosts.

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
- Optionally repeats the operation on configured SSH hosts, in file order.

It does not search recursively, clone missing repositories, switch branches,
push commits, or deploy software to other hosts. The local search paths are
currently fixed; there is no repository exclusion option or dry-run mode.

## Install

Requires Bash, Git, and sed. Remote hosts also need `env`. The installer needs
GNU-compatible `realpath` (with `-m` support); remote mode needs SSH.
The current setup and tests target
Linux. macOS/BSD installation is not verified and may need GNU coreutils.
You can invoke `gpa` from Bash, zsh, or fish.

```sh
mkdir -p ~/repos
git clone https://github.com/samlam369/git-pull-all.git ~/repos/git-pull-all
~/repos/git-pull-all/install.sh
```

Ensure `~/.local/bin` is on PATH. The installer creates a symlink to `bin/gpa`
in this checkout. It needs no root privileges, does not install dependencies,
and does not change shell startup files. Rerunning it is safe; it refuses to
replace unrelated files or symlinks.

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

## SSH hosts

Install `gpa` on each remote machine first and make it available on that user's
noninteractive SSH PATH. Check this with `ssh server-one 'command -v gpa'`.
Authentication and SSH aliases use your existing SSH configuration.

Create `${XDG_CONFIG_HOME:-$HOME/.config}/gpa/hosts`, for example:

```text
# One SSH destination per line, in execution order.
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

Remote mode groups output under a `[HOST]` heading with the configured SSH
destination and its position in the host list, then streams `gpa` or `gpa -v`
using `ssh -q`. Output and diagnostics are indented four spaces beneath each
heading; verbose details keep their additional indentation. Successful hosts
need no extra completion line. Failures add an indented `[FAILED]` line with
the SSH exit status (which can also reflect a remote command failure).
It attempts every host even after a failure. There is no separate
local update unless this machine is also a configured destination. Each remote
uses its own home directory and repository layout.

Preview updated, current, skipped, warning, and failed results without network
activity. The demo includes a mixed-result host, an SSH failure, and an empty
host processed after the failures:

```sh
bash examples/demo-output.sh
bash examples/demo-output.sh -v
```

Output uses four spaces per indentation level, including local summaries and
verbose details. Remote hosts need the updated version for the same spacing.

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

Remote summary colors follow the caller's color choice through `GPA_COLOR`,
without allocating an SSH terminal. Failed repository counts and the attention
list are red; warning counts are yellow. Redirected output is plain by default.
Nonempty `NO_COLOR` on the caller disables colors throughout; a remote's own
`NO_COLOR` can also disable its colors. Remote hosts need the updated `gpa` to
honor the forwarded color choice.

SSH uses its normal host verification and authentication behavior. There is no
built-in connection timeout or batch mode, so a prompt or stalled connection
can delay subsequent hosts. Configure those policies in SSH when needed.

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
pointing at this checkout. The checkout and your host configuration can then
be removed separately if no longer needed.

## Development

The defaults reflect my current workflow and can be revisited as needs change.
Comments record intent, assumptions, and useful tradeoffs so future changes can
be made with context; they do not imply every existing choice is essential or
permanent. When changing behavior, update its documentation and tests too.

```sh
bash -n bin/gpa install.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
git diff --check
```

Tests use temporary homes, local Git fixtures, and mocked SSH. They do not
update user repositories or contact configured hosts. Bug reports with a small
reproduction are welcome; remove personal details from logs first.

## License

[MIT](LICENSE).
