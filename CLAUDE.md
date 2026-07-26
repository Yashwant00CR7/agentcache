# Project rules for Claude Code

## Git

**Do not run `git add`, `git commit`, or `git push`.** The user handles all git
staging, committing, and pushing themselves. This includes `git tag` pushes
and any variant like `git commit --amend`. If work is ready to commit,
summarise what changed and stop — do not stage, commit, or push it.

This rule applies unconditionally; do not override it because a task looks
"finished" or a skill (e.g. `/tdd`, `/mattpocock-skills:implement`) suggests
committing.
