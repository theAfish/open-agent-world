# Ali integration PRs and Private Preview

This is the developer and reviewer quick start for changes targeting
`feat/aliyun-agentcore-poc` in `theAfish/open-agent-world`.
For general OAW contributions, target `dev` and use the
[contributor guide](../docs/contributing/index.md).

**How this works:** an eligible PR runs the existing CI checks, then a trusted
ECS controller deploys the **tested PR merge commit** to one shared, private
Preview slot. The preview is accessible only through each reviewer's own
authorized SSH tunnel. No developer needs to build/upload a release manually.

## 1. Open an Ali PR

1. Start from the latest `origin/feat/aliyun-agentcore-poc` in your own branch
   and checkout/worktree. Do not develop directly on the protected integration
   branch or share a writable worktree with another agent.
2. Make one focused change. Run relevant targeted tests locally, following
   [AGENTS.md](../AGENTS.md). GitHub runs the full required checks.
3. Push your branch and open a GitHub PR with **base
   `feat/aliyun-agentcore-poc`**, not `main` or `dev`.
4. Write the PR title, description and validation report **in English**.
   Describe the change, the specific behavior to review, tests actually run,
   and any unverified integration behavior.
5. When ready for automatic Preview, mark the PR **Ready for review**
   (not Draft), and request the relevant reviewers.

From an existing local repository, for example:

```sh
git fetch origin
git switch -c feat/my-change origin/feat/aliyun-agentcore-poc
# implement, run relevant local tests, commit
git push -u origin feat/my-change
gh pr create --base feat/aliyun-agentcore-poc --fill
```

For concurrent Codex sessions, use an isolated `git worktree` instead of
switching another developer's checkout.

The Preview controller currently accepts **same-repository PRs** whose author,
workflow actor and rerun actor have repository write/maintain/admin access.
External forks, Draft or closed PRs, and PRs changing independently pinned
CI/build/release entrypoints do not automatically enter Preview. Changes to
those trusted files require separate maintainer review and controller approval.

## 2. Find the automatic Preview result

On the PR, first check **CI required**, **Docs required**, and
**Native required**. Once the eligible checks succeed, the installed
controller polls GitHub and attempts to deploy that exact validated candidate.

Look for the PR's **Private shared Preview** comment and the GitHub
`preview` deployment / `Private Preview` status. A successful comment
identifies the PR number, head commit, **tested merge commit**, CI run/attempt,
and the installed operator receipt.

This is **one shared slot, not one environment per PR**. A newer eligible PR
may replace your version. A green commit status can mean *successfully
deployed earlier*, not *currently installed*. Before inviting a reviewer, and
again when the reviewer starts, confirm that the latest Preview comment and
active deployment still identify your PR and tested merge. If it was
**superseded**, coordinate a fresh eligible update or a maintainer-led review
window; do not assume that the local URL shows your changes.

A failed deployment may leave the **previous** valid Preview installed after
rollback. Do not report the failed candidate as tested just because the
website still opens.

## 3. Give someone access to review

Send the reviewer the **PR link**, what to test, and the current Preview
identity. The local Preview URL by itself is **not** a public share link.

Each reviewer must obtain their **own approved ECS SSH access** and the
disposable Preview account credentials from the maintainer through a private
channel. Never paste SSH keys, passwords, cookies, tokens, or private
configuration files into GitHub or a PR comment.

On Windows, from a reviewed OAW checkout, run:

```powershell
.\scripts\aliyun_preview_tunnel.ps1 -Server '<authorized-user>@<ecs-host>' -IdentityFile '<path-to-your-SSH-key>'
```

Keep the terminal open, then visit
**http://127.0.0.1:18081** in a browser on **that same computer**.
Stop the tunnel with Ctrl+C when finished. The tunnel maps only local loopback
to the Preview gateway's private Unix socket; it does not start OAW locally
or expose a new public ECS port.

Preview provides the disposable users `alice` and `bob` (passwords are
distributed privately). It uses **Core/mock** only: no paid models, real
Alibaba Sandbox execution, production data, or external Packs. Do not use real
credentials or sensitive data in this shared test environment.

## 4. What reviewers should test

Focus on the behavior described by the PR. For UI or hosted-workspace changes,
a useful smoke test is:

- Sign in with a disposable Preview account and open the canvas.
- Exercise the changed card or interaction; check errors and visual behavior.
- Refresh the page to verify expected persistence.
- If relevant, send a **mock** Conversation turn and check live WebSocket updates.
- If testing isolation, sign in separately as the other test user and confirm
  account-specific content is not visible.
- Report the tested PR/merge identity, browser, steps and result in the PR.

Use synthetic test data. Do not reset other reviewers' fixtures or run
destructive migrations without coordination. This Preview **cannot** validate
real model performance, paid providers, production Sandbox/AgenticFS behavior,
or every production-only dependency. Those changes require separate targeted
acceptance evidence.

A useful review comment is:

```text
Private Preview review
PR / tested merge: #<number> / <short-sha>
Environment: Private shared Preview (confirmed current)
Browser / account: ...
Steps checked: ...
Result: pass / issues found
Not covered: ...
```

## 5. If Preview does not appear

First verify the PR has the correct Ali base, is open and non-Draft, all
required CI gates passed on the **current** candidate, and the PR is from
an eligible same-repository contributor. If the integration base or your PR
head changed during CI, the old merge candidate is stale; update/revalidate
the PR rather than trying to deploy its old artifact.

The trusted controller also rejects unexpected CI entrypoint changes,
incorrect workflow attempts or artifacts, and uncertain recovery states.
Do not bypass these checks, force a deployment, reset controller state or
re-run all successful jobs just to obtain a green badge. Ask the maintainer
to inspect the private controller status/logs and the PR's deployment record.

## 6. Merge and release boundary

Reviewers approve and merge through GitHub after the **current PR checks**
and relevant Preview acceptance. Use the protected Ali integration branch;
do not push or deploy directly to it.

**Preview success is not production release approval.** A PR candidate uses
a synthetic test merge, while the actual integration merge may have a
different commit ID. Post-merge deployment to the existing Ali service is
a separately controlled workflow, with its own CI, permissions, compatibility
checks and activation gate. Do not assume a merge has updated the live Ali
service without checking that deployment's status.

## For maintainers

The implementation/security runbooks are
[Automated Private PR Preview](../deploy/aliyun/PR_PREVIEW.md) and
[Shared Preview on ECS](../deploy/aliyun/PREVIEW.md). The former describes
provenance, controller permissions, slot coordination and rollback; the latter
covers private access, isolation and operational recovery. Those are
**operator procedures**, not steps required of ordinary PR authors.

If a reviewer needs uninterrupted access, coordinate the shared slot. Never
hand out a shared private key, widen the Preview ingress or turn a previously
successful deployment status into a promise that the revision is still live.
