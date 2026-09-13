# Installer platform validation

`install.sh` provisions the current user's command and remote-mode runtime.
This record distinguishes observed environments from universal support claims.
Actual SSH destinations and private configuration do not belong in this file.

## Platform mapping

| Platform | Detection | System package setup | Python bootstrap |
| --- | --- | --- | --- |
| Debian / Ubuntu | `/etc/os-release` ID is `debian` or `ubuntu` | `apt-get update`, then `apt-get install -y`; sudo if non-root | `python3`, `python3-venv` |
| Termux | `PREFIX/bin/termux-info` exists and is executable; checked first | `PREFIX/bin/pkg install -y`, no sudo | `python`, `python-pip`, `python-ensurepip-wheels` |
| Other | Neither identification matches | No automatic system-package changes | User provides Python 3.10+ and venv/ensurepip |

Git, sed, and coreutils use the same package names on these supported paths.
SSH uses `openssh-client` on Debian/Ubuntu and `openssh` on Termux. Only missing
prerequisites are requested; a Python older than 3.10 produces an explicit
error rather than silently switching interpreters. After provisioning, commands
and Python imports are checked again before creating the managed environment.

Termux has apt-get too, but pkg is its user-facing package tool and handles
its repository/mirror workflow. No Debian package names or privilege model
are inferred from the presence of apt-get. Current Termux splits bootstrap
wheels into `python-ensurepip-wheels`; importing ensurepip and querying its
version is part of the preflight. See the upstream
[Python package definition](https://github.com/termux/termux-packages/blob/master/packages/python/build.sh).

The installer keeps Textual 8.2.8 in
`${XDG_DATA_HOME:-$HOME/.local/share}/gpa/venv`. `gpa -a` selects that interpreter
automatically; callers do not activate it. Local pulls remain Bash-only.
`--no-dependencies` is explicitly link-only and bypasses all runtime setup.
Shell startup files, PATH, SSH credentials, and the hosts list remain user-owned.

## Observed environments (2026-09-14)

The seven configured destinations were inspected with explicit user authority.
Trials use an isolated HOME and XDG_DATA_HOME inside a temporary checkout copy,
without running repository pulls or replacing existing executable links.

| Environment | Count | Python observed | Initial prerequisite state |
| --- | --- | --- | --- |
| Debian 13 | 3 | 3.13.5 | Two ready; one missing ensurepip / venv bootstrap |
| Ubuntu 24.04 | 3 | 3.12.3 | One ready; two missing ensurepip / venv bootstrap |
| Android 16, Termux 0.118.3 | 1 | 3.14.6 | Python, pip, and ensurepip wheels already present |

The Debian group includes a non-root account without passwordless sudo and a
hypervisor host. Pre-provisioned dependencies permit non-root installation
without requesting sudo. Missing Debian/Ubuntu bootstrap packages require
system changes: apt may update the matching Python patch packages as well as
install venv and wheel packages. It does not upgrade the entire system or
remove unrelated packages. These OS changes persist after trial cleanup.

All seven destinations passed first installation, a second run with no package
or pip reinstall, Textual 8.2.8 import from the managed venv, and the installed
command's `--help`. Each also ran the offline demo with that managed runtime;
all produced the expected two successful and two simulated failed hosts, with
exit 1. These demos use mock Git/SSH only, despite being launched on real hosts.

The missing-bootstrap path was exercised on one Debian and two Ubuntu systems.
On Termux, existing prerequisites were retained: an actual
`pkg install -y python python-pip python-ensurepip-wheels` succeeded with no
package install/upgrade, and isolated regression fixtures verify that missing
bootstrap selects pkg with these platform-specific names, never apt-get/sudo.
The pkg run refreshed repository metadata and marked pip/bootstrap packages
as manually installed. A completely bare Termux install was not simulated by
removing working prerequisites. Interactive sudo authentication was not tested;
the non-root installation already had its system prerequisites.

Trial checkout copies, HOME directories, venvs, and logs are removed after
validation. OS packages, package-manager indexes/cache, and the Termux package
marks remain. Existing repository contents and installed gpa links are not
changed by the trials. Future tests must continue to distinguish these effects.

## Follow-up validation procedure

1. Inspect OS identity, package manager, Python version, venv/ensurepip,
   executable availability, and privilege requirements before provisioning.
2. Use temporary HOME, XDG_DATA_HOME, and a checkout copy. Changing HOME does
   not isolate apt/pkg effects; report intended package changes beforehand.
3. Run the normal installer, verify Textual imports from its managed Python,
   and invoke the installed command with `--help` (not a real pull).
4. Rerun installation and confirm the environment is reused. Verify the link
   points at the temporary checkout, then remove only the trial directory.
5. Keep offline tests for package selection, pkg rather than apt/sudo on
   Termux, unknown distributions, and package failures. Record which real
   missing-package paths were exercised versus mocked; never remove working
   system prerequisites merely to simulate a clean machine.
6. Run the required repository checks and retain sanitized evidence here.
   Broader distribution/version support requires new evidence.
