# git-pull-all

A small Bash command for pulling the Git repositories you keep on your machines.
Run `gpa` to update repositories directly under `~` and `~/repos`, or `gpa -a`
to run the same command concurrently on a list of SSH hosts.

GPA runs with your existing user permissions.
Local updates use Bash, Git, and standard command-line tools. Remote
updates add Python on the initiating machine, and the live interface adds
Textual. If the system prerequisites are already available, installation stays
within your user account.

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

## What your environment can run

Choose the capabilities you need based on the tools available in your
environment:

| Capability | Tools needed | If the additional tools are missing |
| --- | --- | --- |
| Update local repositories with `gpa` | Bash, Git, sed, and standard shell utilities | Install the basic tools to use local updates |
| Start concurrent SSH updates with `gpa -a` | Local tools plus SSH client, GNU-compatible `realpath`, and Python 3.10+ | Local updates still work |
| Show the interactive live interface | Remote-update tools plus Textual (installer pins 8.2.8) | Remote updates still work with redirected, plain-text output |
| Receive updates on an SSH host | An accessible SSH service/account, with `gpa`, Bash, Git, sed, and `env` available | Other configured hosts can still receive updates |

Git may also need SSH or credential helpers depending on your repository URLs
and configuration. In every mode, the account performing the pull needs write
access to its repositories and access to their upstreams.

Without Textual, use `gpa -a > gpa.log` for plain-text remote output. A fully
interactive terminal requires Textual and stops with an error if it is missing;
there is no `--plain` flag or automatic missing-Textual fallback.

## Install

Run these commands as your usual user. The installer prepares local and SSH
capabilities; installing missing Debian/Ubuntu system packages requires sudo
when running as non-root.

```sh
mkdir -p ~/repos
git clone https://github.com/samlam369/git-pull-all.git ~/repos/git-pull-all
~/repos/git-pull-all/install.sh
```

Ensure `~/.local/bin` is on your PATH, then run `gpa`.
The installer manages its Python environment automatically.

For setup using existing dependencies or local tools only, and for
troubleshooting, see the [installation guide](docs/install.md).
See [installer platforms](docs/install-platforms.md) for package requirements
and verified environments.

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

### Prepare the remote hosts

Install `gpa` on each remote machine and ensure it is available to the remote
account in a noninteractive SSH session. Check that it starts with
`ssh server-one 'gpa --help'`, replacing `server-one` with your destination.
This does not update repositories or verify access to their Git upstreams.

Connections use your SSH client configuration, including host aliases and
keys. Each remote account uses its own Git configuration and credentials when
pulling repositories. Prepare SSH authentication and host verification before
running GPA: connections that require a prompt will fail.

### Configure destinations

Create `${XDG_CONFIG_HOME:-$HOME/.config}/gpa/hosts`, for example:

```text
# One SSH destination per line, in presentation order.
server-one
user@server-two.example
```

Blank lines and full-line comments are ignored. Put ports and other SSH options
in `~/.ssh/config` and use host aliases here. The installer leaves this file
to you; [examples/hosts](examples/hosts) provides a template.

### Run

```sh
gpa -a                    # update configured hosts
gpa -av                   # include verbose details from every host
```

Only listed destinations are updated. To include this machine, add it as a
destination or run local `gpa` separately.

### What to expect

Hosts run concurrently. On a terminal, the live report keeps them in file
order and follows your terminal colors. Use the mouse wheel, arrow keys,
Page Up/Page Down, or Home/End to scroll while updates run. The complete
report remains in terminal scrollback when finished. Redirected output uses
immediate, host-labelled text instead.

A failed host does not stop the others. Each host has its own repository
summary; the final footer lists hosts needing attention. Ctrl+C stops local
SSH workers and retains partial results, but cannot guarantee cancellation
or rollback of work already running remotely.

See the [SSH reference](docs/ssh-hosts.md) for host-file rules, alternate
configuration paths, output controls, completion states, and connection policy.

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

Engineering decisions are documented beside the relevant implementation.
See the [documentation index](docs/README.md) for usage references and
development records.

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
