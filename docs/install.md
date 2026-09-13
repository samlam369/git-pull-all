# Installation guide

For the standard clone and install commands, see [Install](../README.md#install).
This guide covers setup choices, installation effects, and troubleshooting.

Run the installer as the user who will use GPA. By default it prepares both
local and remote capabilities, even if you only plan to use local updates.
The executable link and managed Python environment are user-level files.
GPA selects the environment automatically when you run it.

Only installing missing **system packages** on Debian/Ubuntu requires root or
sudo. Textual itself is installed inside GPA's private Python environment.
Termux package installation runs as the Termux user.

## Full setup

The one-step installer checks Bash, Git, sed, SSH, GNU-compatible `realpath`,
Python 3.10+, and Python venv/ensurepip support. It identifies the platform
before choosing package names:

- Debian and Ubuntu: `apt-get update` and `apt-get install` for missing
  prerequisites, using `sudo` when not root.
- Termux: `pkg install` with Termux package names, without `sudo` or direct
  `apt-get` calls. Python setup includes `python-pip` and
  `python-ensurepip-wheels`; SSH comes from `openssh`.

Package provisioning uses the network and may prompt for privileges on
Debian/Ubuntu. It can update shared Python packages to satisfy dependencies;
partial system-package changes are not rolled back automatically. There is
no full-system upgrade or autoremove step. See the
[installation validation matrix](install-platforms.md) for tested releases
and the limits of that evidence.

When Debian/Ubuntu needs packages but the current user is non-root and `sudo`
is unavailable, the installer stops with the missing package names and
reference `apt-get` commands labelled as requiring root privileges. It does
not choose another privilege mechanism. Once dependencies are resolved, rerun
`./install.sh` as the original user so the venv and link belong to that user.
Failed package commands retain their original diagnostics and exit status;
the installer also identifies which command failed before stopping.

It then creates `${XDG_DATA_HOME:-$HOME/.local/share}/gpa/venv` and installs the
supported Textual 8.2.8 release there. The environment is owned by this tool;
it avoids modifying system Python or relying on an activated shell environment.
Reruns reuse it when the requested version is already installed.

Other distributions must install the listed commands and Python venv/ensurepip
support using their own package manager before running the normal installer;
it can then create the managed Textual environment without system-package
operations. The existence of `apt-get` alone does not enable provisioning on
an unknown distribution. Alternatively, fully provision Python and Textual
8.2.8 externally and use `install.sh --no-dependencies`.
macOS/BSD setup is not verified and may need GNU coreutils. The flag
skips all dependency checks, package operations, and managed-venv setup, so the
caller owns those dependencies.

```sh
mkdir -p ~/repos
git clone https://github.com/samlam369/git-pull-all.git ~/repos/git-pull-all
~/repos/git-pull-all/install.sh
```

Ensure `~/.local/bin` is on PATH. The installer creates a symlink to `bin/gpa`
in this checkout and does not change shell startup files or host configuration.
Rerunning it is safe; it refuses to replace unrelated files or symlinks.

## Use existing dependencies without package setup

If the basic local tools are available but you cannot install remote-mode
dependencies, you can still install the command for local use:

```sh
~/repos/git-pull-all/install.sh --no-dependencies
gpa
```

This option creates the executable link without dependency checks, package
installation, or venv setup. The link installer itself needs Bash,
GNU-compatible `realpath`, and standard filesystem utilities. You are
responsible for providing the tools needed by the features you use; the option
does not disable remote mode if its dependencies are already available.
Alternatively, run `bash ~/repos/git-pull-all/bin/gpa` directly for local use
without creating the link.

Python venv/ensurepip support is needed for the default installer's managed
environment, not for local updates or an externally provisioned remote runtime.
On Debian/Ubuntu this comes from `python3-venv`; on Termux the installer uses
`python-pip` and `python-ensurepip-wheels`. If it is unavailable, the default
installer stops before creating the link, but the local-use path above remains
available. Rerun the normal installer later to set up the full environment.
