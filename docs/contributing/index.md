# Contribute to OAW

## Branches and pull requests

Use short-lived `feat/*` or `fix/*` branches from current `dev` and open a PR
back to `dev`. `dev` is the integration branch; `main` contains stable release
source. Personal, hosted and cloud deployments use the same source with their
existing configuration and profiles. Do not maintain permanent deployment forks.
Use `release/*` only for an explicitly supported older release line.

Before merging, review the current diff and require **CI required**,
**Docs required**, and **Native required** to pass. These aggregate checks fail
when any dependency fails, is cancelled, or is skipped. The branch must be
up to date with its base. Use squash or merge commits for feature PRs; use a
merge commit for `dev` to `main` promotion so ancestry is preserved. Bring a
necessary stable hotfix back into `dev` through a PR. Never force-push `dev`
or `main`, and never move a published `v*` tag.

After promotion, merge `main` back into `dev` through a PR so the next release
branch includes the promotion commit and satisfies the up-to-date requirement.

Promote tested `dev` to `main` through a release PR, wait for checks on the
resulting `main` commit, then follow [desktop release preparation](../releasing.md).
PR validation of a synthetic merge commit is not proof for another commit.

### Repository administrator setup

The 2026-10-20 audit found both branches unprotected. Batch 0 activated
[Protect dev and main](https://github.com/theAfish/open-agent-world/rules/23912326)
through the GitHub API; both branch endpoints now report `protected: true`.
It requires the three Actions checks, up-to-date branches, one approval, stale
approval dismissal and conversation resolution, and blocks deletion/force pushes
with no bypass actors.
[Protect release tags](https://github.com/theAfish/open-agent-world/rules/25474393)
blocks updates and deletion of `v*` tags, also without bypass actors.
These are repository settings, not settings enabled by cloning this code.

To reproduce or review the settings on another repository, an administrator must:

1. Open **Settings → Rules → Rulesets**, enable the existing ruleset and
   target both `refs/heads/dev` and `refs/heads/main` (or create equivalent rules).
2. Require pull requests, at least one approval, resolution of conversations,
   and dismissal of stale approvals after new commits.
3. Require `CI required`, `Docs required`, and `Native required` from GitHub
   Actions, with branches up to date before merging. Do not add the Pages
   deployment or installer build as merge requirements.
4. Block branch deletion and force pushes; leave routine bypasses disabled.
   Retain merge commits for release integration.
5. Add a tag ruleset for `v*` that blocks updates and deletion. Assign tag
   creation to release maintainers as an operating responsibility; the current
   ruleset does not restrict creation beyond repository write access. Do not
   exempt normal tag publication from the workflow's source/CI gate.

Verify the rules with a test PR whose checks fail and confirm that merging is
blocked. Saving a ruleset or adding these instructions is not that verification.

Use this route when changing the host, its documentation, or a release. To build
an independently installed extension, start with [Build a plugin](../developers/index.md).

## Follow a change through the host

1. [Run from source](../getting-started.md) and choose an isolated development profile.
2. Read [Architecture](../architecture.md) to find the component that owns the behavior.
3. Use the [technical reference](../reference/index.md) for the contract you are changing.
4. Run the relevant tests and the checks in [the CI guide](../../.github/CI.md).
5. Update the affected guide or reference using the [documentation checklist](docs-checklist.md).

## Choose a maintenance task

| Task | Start here | Continue with |
| --- | --- | --- |
| Change canvas behavior | [Canvas control](../canvas-control.md) | [Placement](../layout.md), [performance](../canvas-performance.md), [terrain](../terrain-webgl.md) |
| Change first-use guidance | [Tutorial implementation](../tutorial.md) | [First task and recovery](../product-first-use.md) |
| Change runtime boundaries | [Architecture](../architecture.md) | [Security](../security.md), [enterprise foundations](../enterprise-foundations.md) |
| Package the desktop app | [Desktop development](../desktop.md) | [Release procedure](../releasing.md) |
| Publish a workspace | [Workspace deployment](../deployment.md) | [Plugin deployment contract](../plugin-deployment.md) |
| Write documentation | [Writing checklist](docs-checklist.md) | [Preview, validate, and publish](../developers/documentation.md) |

Proposals, profiling reports, and dated acceptance evidence live in the
[maintainer records](../internal/README.md) on GitHub. Published references explain
the maintained behavior; records explain what was proposed or verified at a
particular revision.
