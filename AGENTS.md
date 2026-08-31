# Agent instructions

Do not commit, push, or merge to `main`.

When you finish a change:

1. Create a feature branch if you are not already on one.
2. Commit with a concise message that explains why the change exists.
3. **Print** fish `git push` and `gh pr create` for the user to run in their own terminal. Do not run them.
4. Stop there. Do not merge the PR and do not push to `main`.

Do this even when the user did not explicitly ask for a commit or PR. A change is not done until the commands to open the PR have been given.

If asked to commit directly to `main` or to merge without review, refuse that path and open a PR instead.

## Do not push from the Cursor sandbox

`git push` and `gh` do not work from this environment (Cursor sandbox proxy). Never attempt them. Never retry with extra permissions, `required_permissions`, or a cloud agent.

Give the user the exact commands for **fish** (this clone’s remote is `eyal0`; `push.autosetupremote=true` so `git push` needs no `-u`). Do not print bash heredocs or `$(…)`. Use a fish multiline quoted `--body`:

```fish
git push
gh pr create --repo eyal0/subzero-ble-homeassistant --title "…" --body '
## Summary
- …

## Test plan
- [ ] …
'
```

Use a cloud agent only when the user asks for one, or the work cannot run in this checkout.

## Branch names

The local branch name and the GitHub PR branch must be the **same string** (the default once the user runs `git push` from this branch).

Do not publish a second remote branch (`cursor/…` or otherwise) for the same change.
