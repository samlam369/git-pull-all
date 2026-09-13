#!/usr/bin/env bash
# Install gpa and its dispatcher dependencies for the current user. Link
# ~/.local/bin/gpa to this checkout so future pulls update the command, and
# keep Textual isolated in a managed user-level virtual environment.
# Invoke with Bash, or execute directly; do not source.
#
# Usage: ./install.sh [--migrate-dotfiles] [--no-dependencies]
# Inputs: HOME and optional XDG_DATA_HOME choose installation paths. By default
# the installer checks Bash, Git, sed, SSH, realpath, Python 3.10+, and venv.
# Debian/Ubuntu use apt-get (through sudo when not root). Termux uses its own
# pkg command and package names without sudo. Other systems must provide the
# prerequisites themselves. The installer then installs Textual 8.2.8 in
# ${XDG_DATA_HOME:-$HOME/.local/share}/gpa/venv. --no-dependencies skips these
# checks and changes for externally provisioned systems and isolated tests.
#
# Effects: package installation may use the network and modify the OS; the
# user-level phase creates/updates the managed venv, creates ~/.local/bin, and
# installs an absolute symlink. The checkout must remain in place. The script
# does not alter shell PATH or host configuration. Repeated runs preserve the
# venv when it already has the requested Textual version.
#
# Existing links to this checkout are accepted without changes. Other paths
# are refused, except the specific legacy link when migration is requested.
# Stdout reports setup actions; errors and missing-platform guidance use stderr.
# Exit status: 0 when ready, 2 for invalid arguments; dependency/setup errors,
# conflicts, and filesystem failures return nonzero without replacing unrelated
# user paths. A partial dependency install is not rolled back automatically.
set -euo pipefail

# Validate every option before creating directories, installing packages, or
# changing links. The options are independent and may appear in either order.
migrate=0 install_dependencies=1
for argument in "$@"; do
    case "$argument" in
        --migrate-dotfiles) migrate=1 ;;
        --no-dependencies) install_dependencies=0 ;;
        *)
            printf 'Usage: install.sh [--migrate-dotfiles] [--no-dependencies]\n' >&2
            exit 2
            ;;
    esac
done

if (( install_dependencies )); then
    # Termux also ships apt-get, but is not Debian: detect its installation
    # prefix before consulting os-release or choosing package names.
    platform=external
    if [[ -n "${PREFIX:-}" && -x "$PREFIX/bin/termux-info" ]]; then
        platform=termux
    elif [[ -r /etc/os-release ]]; then
        # os-release is the OS-owned shell-compatible identity file. Restrict
        # automatic provisioning to the distributions whose mapping we support.
        platform_id=$(. /etc/os-release; printf '%s' "${ID:-}")
        case "$platform_id" in
            debian|ubuntu) platform=debian ;;
        esac
    fi
    ssh_package=openssh-client
    python_packages=(python3 python3-venv)
    venv_packages=(python3-venv)
    if [[ "$platform" == termux ]]; then
        ssh_package=openssh
        python_packages=(python python-pip python-ensurepip-wheels)
        venv_packages=(python-pip python-ensurepip-wheels)
    fi
    declare -a packages=()
    command -v git > /dev/null || packages+=(git)
    command -v sed > /dev/null || packages+=(sed)
    command -v ssh > /dev/null || packages+=("$ssh_package")
    command -v realpath > /dev/null || packages+=(coreutils)
    if ! command -v python3 > /dev/null; then
        packages+=("${python_packages[@]}")
    elif ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
        printf 'gpa: Python 3.10 or newer is required; found: %s\n' \
            "$(python3 --version 2>&1)" >&2
        exit 1
    elif ! python3 -c 'import ensurepip, venv; ensurepip.version()' > /dev/null 2>&1; then
        packages+=("${venv_packages[@]}")
    fi

    if (( ${#packages[@]} )); then
        if [[ "$platform" == external ]]; then
            printf 'gpa: automatic package setup supports Debian, Ubuntu, and Termux only\n' >&2
            printf 'gpa: provide Git, sed, SSH, GNU realpath, and Python 3.10+ with venv/ensurepip, then rerun install.sh\n' >&2
            exit 1
        fi
        declare -a privilege=()
        if [[ "$platform" == termux ]]; then
            if [[ ! -x "$PREFIX/bin/pkg" ]]; then
                printf 'gpa: Termux package manager unavailable: %s/bin/pkg\n' "$PREFIX" >&2
                exit 1
            fi
            printf 'Installing Termux dependencies with pkg: %s\n' "${packages[*]}"
            "$PREFIX/bin/pkg" install -y "${packages[@]}"
        else
            if ! command -v apt-get > /dev/null; then
                printf 'gpa: apt-get is required to install: %s\n' "${packages[*]}" >&2
                exit 1
            fi
            if (( EUID != 0 )); then
                if ! command -v sudo > /dev/null; then
                    printf 'gpa: sudo is required to install: %s\n' "${packages[*]}" >&2
                    exit 1
                fi
                privilege=(sudo)
            fi
            printf 'Installing system dependencies with apt: %s\n' "${packages[*]}"
            "${privilege[@]}" apt-get update
            "${privilege[@]}" apt-get install -y -- "${packages[@]}"
        fi
    fi

    # Validate again after package setup so a distribution package that does
    # not provide the expected command or Python feature fails before linking.
    for dependency in git sed ssh realpath python3; do
        if ! command -v "$dependency" > /dev/null; then
            printf 'gpa: dependency unavailable after setup: %s\n' "$dependency" >&2
            exit 1
        fi
    done
    if ! python3 -c 'import sys, ensurepip, venv; ensurepip.version(); raise SystemExit(sys.version_info < (3, 10))'; then
        printf 'gpa: Python 3.10+ with venv/ensurepip is required\n' >&2
        exit 1
    fi

    data_home=${XDG_DATA_HOME:-$HOME/.local/share}
    venv_dir="$data_home/gpa/venv"
    if [[ ! -x "$venv_dir/bin/python" ]] ||
        ! "$venv_dir/bin/python" -c \
            'import sys, pip; raise SystemExit(sys.version_info < (3, 10))' \
            > /dev/null 2>&1; then
        printf 'Creating managed Python environment: %s\n' "$venv_dir"
        mkdir -p -- "${venv_dir%/*}"
        # This directory is explicitly owned by gpa. --clear repairs stale
        # environments after a system Python upgrade without touching siblings.
        python3 -m venv --clear "$venv_dir"
    fi
    if ! "$venv_dir/bin/python" -c \
        'from importlib.metadata import version; raise SystemExit(version("textual") != "8.2.8")' \
        > /dev/null 2>&1; then
        printf 'Installing Textual 8.2.8 in managed Python environment\n'
        "$venv_dir/bin/python" -m pip install --disable-pip-version-check \
            'textual==8.2.8'
    fi
fi

# Resolve from the script location, so invocation from another working
# directory still links to this checkout. Use its physical directory path
# to keep comparisons consistent when the checkout's parents are symlinks.
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
target="$HOME/.local/bin/gpa"
source_path="$repo_dir/bin/gpa"
mkdir -p -- "$HOME/.local/bin"

# Test for a symlink before testing existence: -e is false for dangling links.
# realpath -m lets us compare relative links and missing legacy destinations.
if [[ -L "$target" ]]; then
    resolved=$(realpath -m -- "$target")
    if [[ "$resolved" == "$source_path" ]]; then
        printf 'Already installed: %s\n' "$target"
        exit 0
    fi
    # Migration is limited to this known previous installation location;
    # the flag is not a general overwrite option. Remove only the link,
    # preserving the old source file if it still exists.
    legacy=$(realpath -m -- "$HOME/.dotfiles/shell-tools/.local/bin/gpa")
    if (( migrate )) && [[ "$resolved" == "$legacy" ]]; then
        rm -- "$target"
    else
        printf 'Refusing to replace unrelated symlink: %s\n' "$target" >&2
        exit 1
    fi
elif [[ -e "$target" ]]; then
    printf 'Refusing to replace existing path: %s\n' "$target" >&2
    exit 1
fi

# Install a link rather than a copy so future checkout updates take effect.
# Leave ln's force option disabled to avoid overwriting an existing file.
ln -s -- "$source_path" "$target"
printf 'Installed: %s -> %s\n' "$target" "$source_path"
