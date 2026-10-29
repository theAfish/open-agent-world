# Contribute to and maintain OAW

Use this path to change the host, maintain documentation, or operate a deployment.
For an independent Card App, use the [developer guide](../developers/index.md).

## Develop a change

1. Branch from the current `origin/dev` in a separate checkout or worktree.
2. [Run from source](../getting-started.md) with an isolated development profile.
3. Read [Architecture](../architecture.md) and the contract for the affected subsystem.
4. Run the smallest relevant checks, then the subsystem tests required by the risk.
5. Update the affected guide and open a PR against `dev`.

Preserve security, authorization, persistence and lifecycle assertions. Report
which checks ran and what remains unverified. Do not infer installed-desktop,
production or native isolation acceptance from unit tests or a browser build.

## CI and repository settings

[The CI guide](../../.github/CI.md) describes workflow-owned checks, including
`CI required`, `Docs required` and `Native required`. Inspect the checks on the
actual PR and its current commit before deciding it is ready to merge. Do not
trigger duplicate full runs for an unchanged commit.

### Repository administrator setup

Rulesets, approval counts and bypass actors are live GitHub settings. A clone
cannot establish their current state, and this guide does not assert a particular
active ruleset. Administrators should inspect **Settings → Rules → Rulesets**
when reviewing policy. Changing documentation or adding workflows does not
change repository permissions. Release publication follows [the release procedure](../releasing.md).

## Operate and extend the host

| Task | Guide |
| --- | --- |
| Build or debug desktop | [Desktop](../desktop.md) |
| Release the application | [Releasing](../releasing.md) |
| Publish a locked workspace | [Deployment](../deployment.md) |
| Run hosted profiles | [Hosting](hosting.md) |
| Change first-use guidance | [Tutorial maintenance](../tutorial.md) |
| Change authentication/context | [Request boundaries](../enterprise-foundations.md), [security](../security.md) |
| Change graph rendering | [Relationship maps](../graph-architecture.md), [canvas performance](../canvas-performance.md) |
| Edit the manual | [Documentation maintenance](../developers/documentation.md) |

[Internal records](../internal/README.md) are non-authoritative historical evidence,
not current operating instructions.
