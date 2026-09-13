#!/usr/bin/env bash
# Install gpa for the current user by linking ~/.local/bin/gpa to bin/gpa
# in this checkout. Invoke with Bash, or execute directly; do not source.
#
# Usage: ./install.sh [--migrate-dotfiles]
# Requires: Bash and GNU-compatible realpath with -m support.
# Effects: creates ~/.local/bin if needed and installs an absolute symlink.
# The checkout must remain in place; pulling it updates the installed command.
# Dependencies, shell PATH, and host configuration are managed separately.
#
# Existing links to this checkout are accepted without changes. Other paths
# are refused, except the specific legacy link when migration is requested.
# Exit status: 0 when installed/already installed, 2 for invalid arguments;
# conflicts return 1 and filesystem failures propagate a nonzero status.
set -euo pipefail

# Validate the invocation before creating directories or changing links.
migrate=0
case "${1:-}" in
    '') ;;
    --migrate-dotfiles) migrate=1 ;;
    *) printf 'Usage: install.sh [--migrate-dotfiles]\n' >&2; exit 2 ;;
esac
(( $# <= 1 )) || exit 2

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
