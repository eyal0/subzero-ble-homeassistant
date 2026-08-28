# Agent instructions

Do not commit, push, or merge to `main`.

When you finish a change:

1. Create a feature branch if you are not already on one.
2. Commit with a concise message that explains why the change exists.
3. Push the branch and open a GitHub pull request.
4. Stop there. Do not merge the PR and do not push to `main`.

Do this even when the user did not explicitly ask for a commit or PR. A change is not done until the PR exists.

If asked to commit directly to `main` or to merge without review, refuse that path and open a PR instead.

## Branch names

The local branch name and the GitHub PR branch must be **the same string**.

This repo uses `push.autosetupremote=true`, so `git push` (no `-u`) publishes a remote branch with the local name. A Cursor cloud agent that opens the PR on a generated name such as `cursor/topic-abcd` leaves a second remote branch; later local pushes will not update that PR.

- If you already created a local branch, the GitHub PR **must** use that exact name. Tell a cloud agent the name; do not accept its default `cursor/…` name.
- If a cloud agent already pushed `cursor/…` and the local name differs, rename the local branch to match (`git branch -m <cursor-name>`).
- Do not create a second remote branch for the same change.

After the remote branch exists, point the local branch at it so `git pull` and `git push` work with no extra arguments:

```bash
git fetch eyal0
git branch --set-upstream-to=eyal0/<branch>
```

The cloud agent usually creates a **new commit** (different SHA, same patch). Local and remote then diverge. Reset local to the PR tip so pull/push are fast-forwards:

```bash
git reset --hard @{u}
```

Do not leave the local branch without an upstream. Do not `git push` until upstream is set and local is not behind the remote.
