#!/usr/bin/env bash
# Link this checkout onto PATH; explicitly migrate the former Stow entry.
set -euo pipefail
migrate=0
case "${1:-}" in
    '') ;;
    --migrate-dotfiles) migrate=1 ;;
    *) printf 'Usage: install.sh [--migrate-dotfiles]\n' >&2; exit 2 ;;
esac
(( $# <= 1 )) || exit 2
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
target="$HOME/.local/bin/gpa"
source_path="$repo_dir/bin/gpa"
mkdir -p -- "$HOME/.local/bin"
if [[ -L "$target" ]]; then
    resolved=$(realpath -m -- "$target")
    if [[ "$resolved" == "$source_path" ]]; then
        printf 'Already installed: %s\n' "$target"
        exit 0
    fi
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
ln -s -- "$source_path" "$target"
printf 'Installed: %s -> %s\n' "$target" "$source_path"
