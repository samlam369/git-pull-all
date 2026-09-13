# git-pull-all

`gpa` pulls Git repositories directly under `~` and `~/repos`, including hidden
directories, directory symlinks, and linked worktrees. It uses `git pull
--ff-only` and summarizes updates, unchanged repositories, skips, and failures.
Branches without an upstream are skipped. Git terminal prompts are disabled.
Existing Git configuration still applies, including autostash behavior.

## Install

Requires Bash, Git, sed, and GNU-compatible `realpath` for the installer;
remote mode also requires SSH. Both zsh and fish can invoke the executable.

```sh
mkdir -p ~/repos
git clone https://github.com/samlam369/git-pull-all.git ~/repos/git-pull-all
~/repos/git-pull-all/install.sh
```

Ensure `~/.local/bin` is on PATH, including in noninteractive SSH shells.
Installation links `~/.local/bin/gpa` to this checkout. Rerunning is safe;
unrelated files and symlinks are never overwritten. To replace the former
`~/.dotfiles/shell-tools/.local/bin/gpa` Stow symlink (including a dangling one):

```sh
~/repos/git-pull-all/install.sh --migrate-dotfiles
```

## Usage

```sh
gpa                  # local repositories, compact summary
gpa -v               # local repositories, detailed output
gpa -a               # configured SSH hosts, sequentially
gpa -av              # remote verbose output (-va and -a -v also work)
gpa --all --verbose
gpa --help
```

Remote mode reads `${XDG_CONFIG_HOME:-$HOME/.config}/gpa/hosts`.
Set `GPA_HOSTS_FILE` to override that path. One SSH destination per line;
blank lines and full-line comments are ignored. Destinations must start with
an ASCII letter, digit, or underscore and otherwise contain only letters,
digits, underscores, dots, `@`, colons, or hyphens. Put SSH ports and other
options in `~/.ssh/config`. Missing, empty, or invalid configuration fails
before connecting to any host. See `examples/hosts` for the format.

Personal configuration can be managed by dotfiles; the installer does not
create or overwrite it. If using the samlam369 dotfiles shell-tools package,
Stow supplies this configuration. Otherwise copy and edit the example.

Remote mode prints the remote user and hostname, then runs `gpa` (or `gpa -v`)
using `ssh -q`. It attempts every configured host even after a failure, and does
not also run a separate local update. Each remote must already have `gpa` on
its noninteractive PATH. This command does not deploy or install remote tools.

Exit codes: 0 for success (including skips), 1 for any pull/SSH failure,
2 for invalid arguments or remote host configuration.

## Updates and migration

Pull this repository to update the installed command; no reinstall or Stow
operation is required. Because this checkout is under `~/repos`, local `gpa`
also discovers it. The Bash implementation is parsed into a function before
updates start, so an invocation finishes using the code it started with.
New code takes effect on the next invocation.

For a multi-host migration, clone and test this repository first, migrate each
host's entry point explicitly, then update dotfiles and link its host config.
Do not use `gpa -a` as a deployment mechanism. Uninstall by removing only the
`~/.local/bin/gpa` symlink pointing at this checkout.

## Development

```sh
bash -n bin/gpa install.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
git diff --check
```

Tests use temporary homes, local Git fixtures, and mocked SSH, without updating
user repositories or contacting hosts. Originally extracted from
`samlam369/dotfiles` (`shell-tools/.local/bin/gpa`); multi-host execution was
introduced in this repository before being committed to dotfiles.
