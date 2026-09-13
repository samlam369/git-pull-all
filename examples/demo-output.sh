#!/usr/bin/env bash
# Preview actual gpa formatting with simulated Git and SSH results.
# Invoke with bash examples/demo-output.sh [-v|--verbose]; do not source.
# Uses Bash, sed, dirname, mktemp, mkdir, chmod, cat, and rm. Resolves bin/gpa
# in this checkout and overrides HOME, PATH, and GPA_HOSTS_FILE with temporary
# fixtures. Mock SSH invokes gpa locally; mock Git only reads/writes fixtures.
# No real SSH or Git runs, and user repositories/configuration are untouched.
# Fixtures cover successful updates, skips, warnings, pull and SSH failures,
# and an empty host after failures. Repeated runs start fresh and clean up on
# exit. Output follows gpa's color and progress policies, including NO_COLOR;
# remote output is piped just as in real -a mode. Exit 1 is expected; invalid
# options exit 2, and setup errors stop immediately. Verbosity is forwarded
# to remote fixtures.
set -eu

case "${1:-}" in
    '') demo_flags=() ;;
    -v|--verbose) demo_flags=(-v) ;;
    *) printf 'Usage: bash examples/demo-output.sh [-v|--verbose]\n' >&2; exit 2 ;;
esac
if (( $# > 1 )); then
    printf 'Usage: bash examples/demo-output.sh [-v|--verbose]\n' >&2
    exit 2
fi
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
demo_dir=$(mktemp -d)
trap 'rm -rf "$demo_dir"' EXIT
export GPA_DEMO_ROOT="$demo_dir" GPA_DEMO_EXECUTABLE="$project_dir/bin/gpa"
mkdir -p "$demo_dir/bin" "$demo_dir/server-empty"
for repo in app current scratch detached; do
    mkdir -p "$demo_dir/server-ok/repos/$repo/.git"
done
for repo in diverged forced restored warning; do
    mkdir -p "$demo_dir/server-mixed/repos/$repo/.git"
done
printf '%s\n' server-ok server-mixed server-offline server-empty > "$demo_dir/hosts"
cat > "$demo_dir/bin/ssh" <<'MOCK'
#!/usr/bin/env bash
# Demo-only SSH replacement: accept the fixed gpa command, dispatch locally,
# or simulate a connection failure. No host input is evaluated as shell code.
case "$2" in
    server-offline)
        printf '%s\n' 'ssh: connection to server-offline timed out' >&2
        exit 255 ;;
    server-ok|server-mixed|server-empty)
        case "$3" in
            'env GPA_COLOR=always GPA_PROGRESS='*) color=always ;;
            'env GPA_COLOR=never GPA_PROGRESS='*) color=never ;;
            *) exit 2 ;;
        esac
        case "$3" in
            *' GPA_PROGRESS=always gpa'|*' GPA_PROGRESS=always gpa -v') progress=always ;;
            *' GPA_PROGRESS=never gpa'|*' GPA_PROGRESS=never gpa -v') progress=never ;;
            *) exit 2 ;;
        esac
        flags=()
        [[ "$3" != *' -v' ]] || flags=(-v)
        HOME="$GPA_DEMO_ROOT/$2" GPA_COLOR="$color" GPA_PROGRESS="$progress" bash "$GPA_DEMO_EXECUTABLE" "${flags[@]}" ;;
    *) exit 2 ;;
esac
MOCK
cat > "$demo_dir/bin/git" <<'MOCK'
#!/usr/bin/env bash
# Demo-only Git replacement for gpa's -C probes and pull. A marker in the
# temporary repository simulates HEAD movement; no Git or network is used.
repo=$2
name=${repo##*/}
shift 2
case "$*" in
    'branch --show-current')
        [[ "$name" == detached ]] || printf 'main\n' ;;
    'rev-parse --short HEAD') printf 'aaaaaaa\n' ;;
    'rev-parse --abbrev-ref @{upstream}')
        [[ "$name" != scratch && "$name" != detached ]] || exit 1
        printf 'origin/main\n' ;;
    'rev-parse HEAD')
        if [[ -f "$repo/.demo-pulled" ]]; then printf 'bbbbbbb\n'; else printf 'aaaaaaa\n'; fi ;;
    'pull --ff-only')
        case "$name" in
            diverged)
                printf 'fatal: Not possible to fast-forward, aborting.\n'
                exit 128 ;;
            current) printf 'Already up to date.\n' ;;
            *)
                : > "$repo/.demo-pulled"
                case "$name" in
                    forced) printf ' + aaaaaaa...bbbbbbb main -> origin/main (forced update)\n' ;;
                    restored) printf 'Created autostash: ccccccc\n' ;;
                    warning) printf 'warning: simulated Git warning for this demo\n' ;;
                esac
                printf 'Updating aaaaaaa..bbbbbbb\nFast-forward\n 1 file changed, 2 insertions(+)\n'
                [[ "$name" != restored ]] || printf 'Applied autostash.\n' ;;
        esac ;;
    *) exit 2 ;;
esac
exit 0
MOCK
chmod +x "$demo_dir/bin/ssh" "$demo_dir/bin/git"
PATH="$demo_dir/bin:$PATH" GPA_HOSTS_FILE="$demo_dir/hosts" bash "$project_dir/bin/gpa" -a "${demo_flags[@]}"
