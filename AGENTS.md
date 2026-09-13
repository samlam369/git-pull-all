# Development guide

- Keep the public command `gpa` and preserve local summary behavior.
- Keep personal hostnames and credentials out of this repository.
- Never use live SSH or update user repositories to run tests.
- Validate with `bash -n bin/gpa install.sh`,
  `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`,
  and `git diff --check`.
- Use atomic Conventional Commits with lowercase scopes and subjects,
  motivation paragraphs, and implementation bullets. Stage explicit paths.
