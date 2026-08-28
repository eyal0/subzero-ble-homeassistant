# Agent instructions

Do not commit, push, or merge to `main`.

When you finish a change:

1. Create a feature branch if you are not already on one.
2. Commit with a concise message that explains why the change exists.
3. From **this checkout**, `git push` and open a GitHub pull request (`gh pr create`).
4. Stop there. Do not merge the PR and do not push to `main`.

Do this even when the user did not explicitly ask for a commit or PR. A change is not done until the PR exists.

If asked to commit directly to `main` or to merge without review, refuse that path and open a PR instead.

## Push and open the PR here

Do **not** spawn a Cursor cloud agent to re-apply the same patch and open the PR. That duplicates the commit (different SHA), is slow, and breaks `git pull` / `git push` until you reset.

This clone’s GitHub remote is `eyal0`. `push.autosetupremote=true` means `git push` (no `-u`) creates `eyal0/<local-branch>` and sets upstream. Then:

```bash
git push
gh pr create --repo eyal0/subzero-ble-homeassistant --title "…" --body "…"
```

If `git push` or `gh` fails in this environment (sandbox, bad token), **print those commands for the user to run in their own terminal**. Do not start a cloud agent as the fallback.

Use a cloud agent only when the user asks for one, or the work cannot run in this checkout.

## Branch names

The local branch name and the GitHub PR branch must be the **same string** (the default once you `git push` from this branch).

Do not publish a second remote branch (`cursor/…` or otherwise) for the same change.
