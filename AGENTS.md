# Agent development guide

These conventions apply to the entire repository, regardless of the agent or
editor used. Read this file and the affected scripts before making changes.

## Purpose and ownership

`gpa` is a small personal Bash tool for updating Git checkouts locally and on
configured SSH hosts. Keep it understandable and usable independently of the
maintainer's environment. Prefer focused changes over adding infrastructure.

- `bin/gpa` owns argument parsing, repository discovery, pulls, SSH dispatch,
  and result summaries. Bash, zsh, and fish invoke the same executable.
- `install.sh` owns dependency preflight/provisioning, the managed Textual
  virtual environment, the user-level executable symlink, and the explicitly
  requested legacy-link migration. It does not own shell PATH or host
  configuration.
- `examples/hosts` documents the host-file format using fictional destinations.
- `tests/test_gpa.py` verifies behavior with temporary homes, local Git fixtures,
  and mocked SSH.
- `README.md` is the public usage contract; `AGENTS.md` is the development
  contract. Explain implementation decisions next to the relevant code.
  Keep README focused on getting started and setting user expectations;
  detailed setup belongs in `docs/install.md` and SSH behavior in
  `docs/ssh-hosts.md`. Keep engineering
  rationale beside the implementation and validation history in development
  records rather than repeating them in the user guide.

## Decision records support informed changes

Document intent so future maintainers can make informed decisions, including
changing or removing the current design. An explanation is context, not proof
that a choice was necessary, optimal, or should be permanent.

Distinguish observed behavior, explicit user requirements, current assumptions,
and implementation preferences. A choice may reflect convenience, a personal
workflow, or a reasonable default rather than a strong technical constraint.
Say so when known. If the original rationale is unknown, acknowledge that
instead of inventing a justification. Mention tradeoffs or alternatives when
they help assess a future change; exhaustive defenses are not required.

Treat the current behavior described below as a compatibility baseline, not a
ban on redesign. When a task calls for a change, assess affected users, safety,
and tests, then update the implementation and documentation together. Routine
implementation choices can be reconsidered within the task's scope without
separate permission. Explicit user constraints, privacy requirements, and
protection of unrelated user data still apply.

## Script frontmatter: explain intent first

Every maintained executable script must start with a descriptive comment block
immediately after its shebang. Python modules and test suites should use a
module docstring for the same purpose. Here, frontmatter means ordinary source
comments or a docstring, not YAML or executable metadata.

The header must let a new reader understand why the file exists and how it
fits into the tool without first tracing its implementation. Cover:

- **Role and intent:** the problem being solved, intended outcome, and known
  assumptions or tradeoffs behind important choices. This is more useful than a list of steps.
- **Invocation:** who calls it, when, its command syntax, and whether it is
  executed or sourced. Identify automatic callers if any.
- **Inputs and configuration:** arguments, relevant environment variables,
  configuration paths, defaults, and precedence rules.
- **Interactions and ownership:** cooperating scripts, commands, and files;
  what this script manages and what its caller or another component manages.
- **Dependencies:** runtime, external commands, authentication, and material
  platform assumptions. Distinguish installation and optional-mode needs.
- **Effects and repeatability:** filesystem or Git changes, network activity,
  overwrite rules, and what happens on repeated or partial execution.
- **Outputs and failures:** stdout/stderr meaning, exit statuses, and whether
  failures stop processing or are collected while later work continues.

Use concise labeled prose where helpful; these are content requirements, not
mandatory field names. Scale detail to the script. Small helpers may combine
related points, but must still explain intent and meaningful side effects.
Do not claim guarantees the implementation does not provide.

For example, a useful installer explanation is: “Link the checkout so future
pulls update the installed command; refuse unrelated paths to preserve user
files.” “Create a symlink” alone does not explain the intent.

## Inline comments and documentation maintenance

- Explain the context behind non-obvious choices: assumptions, tradeoffs,
  ordering dependencies, compatibility concerns, or failure handling. If an
  alternative would be incorrect, explain under which conditions. Avoid narrating obvious assignments and commands.
- Give nontrivial functions or phases a short purpose comment when their
  name and header do not explain their contract. Document surprising inputs,
  outputs, shared state, or side effects where they matter.
- Keep useful context for subtle behavior, such as checking dangling
  symlinks before existence, validating all hosts before SSH, or parsing the
  implementation before its checkout can update itself. Revisit those
  explanations when their assumptions or implementation change.
- Update headers, nearby comments, help text, examples, and README together
  when a change affects their claims. Fix stale explanations when touching
  the relevant code; do not add speculative behavior or unrelated rewrites.
- Use plain English, concrete names, and sufficient context for an unfamiliar
  maintainer. Comment volume is not a quality metric: prioritize intent,
  correctness, and information the code alone cannot convey.

## Current behavior and implementation guidance

These describe today's behavior and considerations for changing it. Preserve
compatibility during unrelated work; intentional changes should account for
the effects below rather than treating the existing mechanism as mandatory.

- Keep the public command `gpa`, existing flags, and local summary behavior
  compatible unless the task explicitly changes that contract.
- Discovery currently covers direct children of HOME and HOME/repos,
  including hidden directories, directory symlinks, and linked worktrees.
- Pulls currently use `--ff-only` and skip branches without an upstream.
  Changes to pull policy, failure aggregation, or exit statuses affect user
  expectations and should be reflected in documentation and tests.
- Remote mode validates the complete host list before connecting, invokes a
  fixed remote command, and continues after a host failure. Keep host data
  out of shell source; do not introduce eval or interpolate it into commands.
- The tool can update its own checkout while it runs. The current function
  boundary loads the implementation before pulls start; any replacement
  approach should account for that self-update scenario.
- Quote shell expansions and use arrays for argument lists. Account for
  whitespace, dangling symlinks, and missing paths where applicable.
- Do not apply `set -e` mechanically to the main command: failure aggregation
  and arithmetic statuses need deliberate handling. The installer uses
  strict mode because its filesystem operations should stop on failure.
- The installer supports repeat runs, a narrowly scoped migration, platform
  detection for Debian/Ubuntu apt and Termux pkg provisioning, and a pinned
  Textual release in a managed venv. Keep observed releases and verification
  limits in `docs/install-platforms.md`; do not infer a distribution from the
  presence of `apt-get`. Termux uses pkg without sudo. `--no-dependencies`
  keeps externally provisioned and test environments free of package changes.
  Protect unrelated user files, report
  network/system effects before running them, and do not guess commands for
  other platforms.
- Keep dependencies and platform claims explicit. Do not claim portability
  beyond what has been checked or silently add setup/network side effects.

## Privacy and scope

- Keep actual hostnames, addresses, credentials, personal configuration, and
  runtime output out of tracked files. Use fictional examples and fixtures.
- Public documentation must stand on its own. Do not name or link private
  repositories or describe maintainer-specific migration history there.
- Never use live SSH or update user repositories to run tests. Use isolated
  fixtures; documentation edits are not authorization to run the real tool.
- Preserve unrelated worktree changes. Follow the user's current review,
  commit, and push instructions; a local edit does not imply permission to
  change visibility, deploy hosts, or rewrite published history.

## Validation and handoff

Run these required checks from the repository root before handing off changes:

```sh
bash -n bin/gpa install.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
git diff --check
```

For behavior changes, add meaningful regression coverage using isolated
fixtures, including relevant failure paths. Comment-only edits do not need
new tests. Review the diff to ensure documentation matches actual behavior.
Report what changed, validation results, and whether changes were committed
or pushed. Identify any unverified behavior without overstating guarantees.
For the concurrent-host feature, keep readiness, known UAC gaps, and the
platform test matrix in `docs/parallel-hosts.md`. Environment validation uses
temporary homes/checkouts and may use real SSH only with explicit authorization;
it must never update user repositories.

## Git conventions

- Use one logical change per commit; separate unrelated changes.
- Stage explicit file paths, never `git add .` or `git add -A`.
- Use Conventional Commits: `<type>(<scope>): <subject>`. Keep the scope and
  subject lowercase, use an imperative subject, omit its trailing period,
  and aim for a header of at most 72 characters.
- Follow the header with a motivation paragraph explaining why, then bullets
  describing what changed and how. Wrap body text around 72 characters.
- Use precise technical terminology and optional reference trailers when
  useful. Do not introduce private repository references into new commits.
