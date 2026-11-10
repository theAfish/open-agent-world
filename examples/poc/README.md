# Team POC development

[legion.json](legion.json) contains the three workspaces from the deployed Alibaba
**实验工作台**, replacing the single generic Knowledge Base starter:

| Workspace | Included configuration |
| --- | --- |
| Lab Manager | Knowledge Base, DSH administrator and Chat |
| XRD 分析工作台 | Spectrum/structure canvases, matching, Jev, result reviewers and history |
| MatCreator research | Native Agent, conversation, Sandbox, graph, jobs, artifacts and skills |

Layouts and cross-workspace connections come from the published application.
Initial MatCreator skills come from this repository's bundled standard library.
Customer files, knowledge databases, results, conversations, accounts, credentials
and Alibaba bindings are excluded. Each developer supplies local model settings
and test inputs. No ECS access is needed.

## First run

Install the prerequisites in [source setup](../../docs/getting-started.md).
Third-party Packs and exact versions are declared in [packs.toml](packs.toml).
`setup` detects what is missing and downloads it from the configured Store,
using the normal Pack installer and integrity/compatibility checks. DSH remains
an independent Pack, not vendored OAW code.

```sh
git clone --branch feat/aliyun-agentcore-poc https://github.com/theAfish/open-agent-world.git
cd open-agent-world
git switch -c feat/my-poc
python scripts/poc.py setup
python scripts/poc.py start
```

`setup` prepares Knowledge Base, XRD, SQLite, MatCreator, Structure Viewer and the
ADK/LiteLLM SDKs through the existing source installer, then prepares the pinned
third-party Packs in this development profile. Initial scientific dependency setup
can take time; it is not repeated on start. The DSH CLI/executable uses its normal
Library preparation when first needed.

To inspect Pack versions without downloading, run `python scripts/poc.py deps`.
Matching installed versions are reused without contacting the Store. After
pulling changes to `packs.toml`, stop the launcher and rerun `setup` (or
`python scripts/poc.py deps --install` if only third-party Packs changed).
Setup will not downgrade a newer installed Pack; use a separate `--profile NAME`
when comparing versions. It never substitutes an older Store release.

For a private/unpublished Pack or offline third-party installation, supply the
exact trusted archive with `setup --pack PATH` (repeatable). Its identity and
version must match `packs.toml`. Keep downloaded archives outside Git. The normal
`OPEN_AGENT_WORLD_MARKETPLACE_URL` override is respected; an empty value disables
Store access. Missing/unavailable versions produce an actionable error before
the workbench is imported.

Open the printed URL, configure your model in **Settings**, then edit any of the
three Legions or open its Workspace. DSH uses `oaw:default`; `host-default` uses
local restrictions on your computer and the configured isolation policy on
Alibaba. The ordinary Sandbox uses Shared workspace scope and has networking
enabled, with no server directory or Volume binding.

State lives under `.open-agent-world/development/profiles/poc/`, ignored by Git.
Personal/default data is separate. Use `--profile NAME` consistently for another
profile. `start` never installs, resets, or deploys.

Already used the old Lab Manager example? Stop it, rerun setup for dependencies,
then import the new workbench beside it:

```sh
python scripts/poc.py apply --template examples/poc/legion.json
python scripts/poc.py start
```

`init`/`setup` preserve existing cards. `apply` creates new instances and retains
existing data/settings. Remove the old generic Legion in the editor when ready;
otherwise it is also included when previewing all top-level Legions.

## Edit, save and preview

Edit the canvas, connections, instructions and Workspace layouts locally.
Frontend code hot reloads; restart after backend changes. Stop with Ctrl+C before
running offline commands:

```sh
python scripts/poc.py list
python scripts/poc.py save examples/poc/legion.json --include-state "Skills & knowledge"
python scripts/poc.py check
python scripts/poc.py preview
```

`save` and `preview` select all top-level Legions by default. To select a subset,
repeat `--legion "NAME"` (IDs also work). Keep connected Legions together: the
DSH administrator has cross-workspace links. External dependencies are rejected
rather than dropped. Portable keys and definition identity survive sharing;
repeated saves without edits produce no diff.

`--include-state "Skills & knowledge"` includes the container and its owned skill
cards, preserving the reusable MatCreator library. Other document contents are
omitted by default. Review any changes to explicitly shared skills before
committing. Knowledge Base projects, Space definitions and records are not
portable template content; import synthetic files such as [sample.md](sample.md)
locally. Do not put customer data or keys into shared instructions/documents.

`check` instantiates clean temporary data and validates every Workspace's
publication contract. `preview` opens the real published user UI with its
workspace switcher and a generated local password. Its private local snapshot
keeps preview writes independent from the editor. For UI-only testing:

```sh
python scripts/poc.py preview --agent-runtime core.mock
```

Snapshots under `.open-agent-world/development/previews/poc/` may contain your
own settings/test data and are never Git artifacts. `--prepare-only` creates one
without launching; resume it with:

```sh
python scripts/dev.py --profile poc --deployment <printed-snapshot-path>
```

Local preview listens on `127.0.0.1`. Model calls need your own credentials;
scientific calculations and shell execution need their normal local runtimes.
Alibaba Sandbox/AgenticFS isolation remains a separate cloud acceptance test.

## Share through GitHub

```sh
git add examples/poc/legion.json
git commit -m "Update the team experimental workbench"
git push -u origin feat/my-poc
gh pr create --base feat/aliyun-agentcore-poc --fill
```

Follow the [Ali PR workflow](../../.github/ALI_PR_GUIDE.md): review and merge,
required CI, then maintainer **Review deployments → Approve and deploy**.
The trusted ECS controller installs the verified artifact. Developers do not
enter SHAs or run server commands. **Ali integration deployment** confirms actual
installation; the approval job alone does not.

The hosted admin selector offers **Git: 实验工作台** with all three regions.
Admins select target accounts; merging never silently replaces existing user
workspaces. Structural or Pack-version changes retain the existing explicit
migration/new-workspace process.

Teammates pull, stop their launcher and use `apply` to compare a new instance
with existing work. Missing/incompatible Packs are errors, never silently
replaced with other agents. Single-Legion presets remain supported, e.g.
`python scripts/poc.py apply --preset knowledge.base.research`.
