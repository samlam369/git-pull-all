# SSH reference

For initial setup and commands, start with [SSH hosts in the README](../README.md#ssh-hosts).
This reference describes configuration and observable behavior of `gpa -a`.

## Host configuration

The default file is `${XDG_CONFIG_HOME:-$HOME/.config}/gpa/hosts`.
A nonempty `GPA_HOSTS_FILE` overrides it:

```sh
GPA_HOSTS_FILE=/path/to/hosts gpa -a
```

Each destination occupies a whole line without surrounding whitespace.
Blank lines and full-line comments are ignored. Destinations start with an
ASCII letter, digit, or underscore and otherwise contain only letters,
digits, underscores, dots, `@`, colons, or hyphens. Put ports and SSH options
in `~/.ssh/config`; use aliases for addresses outside this format.
Missing, empty, or invalid configuration fails before any host is contacted.
The installer does not create or overwrite the host file.

`gpa -av`, `gpa -va`, and `gpa --all --verbose` are equivalent: verbose mode
applies to every configured host. Remote mode does not separately update the
initiating machine unless it is also listed as a destination.

## Live and redirected output

When stdin, stdout, and stderr are all terminals, the live interface shows
an overall status line and growing host sections in file order. Finishing
early does not move a host's section. The interface closes when all output
is drained and prints the complete grouped report into terminal scrollback.

The live and final reports share headings such as
`[HOST   ] server-one  (1/7)` and four-space repository indentation. The live
heading also shows the current host state: `starting`, `running`, `completed`,
`failed`, or `interrupted`.

The interface follows the terminal's foreground, background, and palette;
the scrollbar uses the main text color. Page Up/Page Down, arrow keys,
Home/End, and the mouse wheel scroll the report. Content growing in earlier
host sections preserves a stationary reader's position within their section.

Redirecting any standard stream selects plain output, for example:

```sh
gpa -a > gpa.log
```

Each line streams immediately with `[destination]` attribution, including
blank lines and verbose details. SSH diagnostics are attributed too and remain
on stderr. Plain mode emits no cursor controls and does not replay host output
at completion. It requires Python but not Textual; a fully interactive terminal
requires Textual and reports an error if it is unavailable.

## Completion and interruption

A repository failure does not finish its host; the remote command's exit
status determines host completion. A disconnect retains received output,
including any pending FETCH line, and reports the host failure. Other hosts
continue running.

The footer reports host command completion and names hosts to revisit:

```text
gpa hosts: 2/4 completed
    Needs attention: server-mixed, server-offline
```

Completed means the remote command exited successfully, including repository
skips and warnings. The attention list covers nonzero SSH/remote command exits;
those hosts may still have successful pulls. Repository counts and warnings
stay in each host's summary. When every host completes, only the first footer
line is printed. A failed host makes the calling GPA exit with status 1.

Ctrl+C marks unfinished hosts interrupted, retains received output, stops
local SSH workers, and exits 130. Closing local SSH does not guarantee
cancellation or rollback of a remote Git operation.

## Progress, colors, and older remote versions

Updated remote versions replace each FETCH line with its repository result.
Older versions still stream ordinary text immediately, but FETCH and its final
result may appear on separate lines. Compatibility handling does not run a
pull twice.

Redirects omit FETCH by default. `GPA_PROGRESS=always` includes it as a readable
attributed line; `GPA_PROGRESS=never` suppresses it.

Remote summary colors follow the caller's `GPA_COLOR` choice. Failed repository
counts and the attention list are red; warning counts are yellow. Redirected
output is plain by default. Nonempty `NO_COLOR` on the caller disables colors
throughout; a remote's own `NO_COLOR` can also disable its colors. Remote hosts
need the updated GPA to honor the forwarded color choice.

## Connection policy

SSH uses normal host verification with `BatchMode=yes`: authentication or
verification requiring a prompt fails that host instead of blocking the
report. Prepare keys and known hosts through your normal SSH workflow.
Remote commands receive no terminal input, and GPA does not allocate an SSH
terminal. There is no built-in timeout or retry; configure timeout policy in
your SSH configuration.
