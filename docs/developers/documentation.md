# Maintain this documentation site

The site uses Material for MkDocs and GitHub Pages. Markdown in `docs/` remains the source of truth; plugin-specific guides stay beside their packages. There is no second Wiki to synchronize.

Start with the [writing checklist](../contributing/docs-checklist.md) to choose a
reader, location, language, and page structure. This page covers the build workflow.

## Preview locally

From the repository root, install the documentation tools in a separate environment:

```sh
uv venv --python 3.12 .tmp/docs-venv
```

Windows:

```powershell
uv pip install --python .tmp/docs-venv/Scripts/python.exe -r scripts/docs-requirements.txt
./.tmp/docs-venv/Scripts/python.exe scripts/docs.py serve
```

Linux/macOS:

```sh
uv pip install --python .tmp/docs-venv/bin/python -r scripts/docs-requirements.txt
.tmp/docs-venv/bin/python scripts/docs.py serve
```

The preview prints its local URL. Replace `serve` with `check` to run the same
validation as CI, using the same environment's Python:

```powershell
# Windows, from the repository root
./.tmp/docs-venv/Scripts/python.exe scripts/docs.py check
```

```sh
# Linux/macOS, from the repository root
.tmp/docs-venv/bin/python scripts/docs.py check
```

This checks source inventory, headings and language separation, runs publishing
tests, builds with `--strict`, then checks generated links, anchors, assets, and
search boundaries. Output goes to `.tmp/docs-site` and is not committed. The
documentation environment does not require the backend, frontend, or model access.

## Diagnose a failed check

| Failure | Repair |
| --- | --- |
| Page missing from navigation | Add it under the correct audience in `mkdocs.yml`; move historical records to `internal/` |
| Chinese prose in an English page | Create or update the adjacent `.zh-CN.md` page and add language links |
| Title count or heading level | Use one `#` title and consecutive section levels; examples belong in fenced code |
| Missing repository link | Correct the relative path or commit the intended source/asset |
| Ignored target or incorrect case | Match the checkout's exact filename; do not link local build/test artifacts |
| Missing generated anchor or asset | Update the link and its target together, then rebuild |
| Tutorial examples fail | Inspect the separate tutorial job; reproduce with the commands in [Test and distribute](testing.md) |

Keep strict warnings enabled. An exclusion is a content decision, not a workaround
for a broken public page. Filename checks also run on Windows so mistakes are found
before the Linux build.

## Theme

`docs/stylesheets/extra.css` maps the app palette in [`frontend/src/theme.css`](../../frontend/src/theme.css) to Material's light (`default`) and dark (`slate`) schemes. Keep the warm gray surfaces, copper accents, and system font aligned when changing either theme. Documentation uses the stronger accent for links and the secondary ink for muted reading text so both remain legible on page, card, and code backgrounds.

Keep header surface colors separate from link and button colors. Check both themes on the home page, a guide with tables, a developer page with code, search results, and the mobile navigation drawer. Verify theme selection survives navigation and reload, and check text contrast in normal, hover, and focus states.

## Put content in the right place

| Audience | Location | Writing rule |
| --- | --- | --- |
| Everyday users | `docs/user-guide/` | Actions, visible results, and recovery; no implementation walkthroughs |
| New plugin authors | `docs/developers/` | Ordered lessons with runnable examples and expected results |
| Experienced developers | Technical reference navigation | Contracts, lifecycle, architecture, and detailed behavior |
| Host contributors | `docs/contributing/` | Host development, documentation, and release workflows |
| Maintainers recording evidence or future work | `docs/internal/` | Date and label proposals, investigations, and validation records; exclude from the site |

Keep English as the primary language in `topic.md`, with Chinese prose in a
separate `topic.zh-CN.md`. Link translations explicitly and label English-only
destinations on Chinese pages. Avoid sending a new user from an ordinary task
straight into an API contract. Existing root-level reference pages keep their
URLs; new reference pages go in `docs/reference/`.

Add published pages to `mkdocs.yml`. Unlisted pages and broken local links fail
validation. `internal/` stays out of generated HTML, search, and the bundled manual.
General search covers landing pages, installation, user/developer guides, and
contribution guides; detailed contracts remain accessible through Technical reference.

`scripts/docs_hooks.py` converts repository source links and excluded-record links
to GitHub while leaving normal documentation links for MkDocs to validate. Targets
must exist with exact filename case and be tracked or eligible for Git. Ignored
local outputs cannot become broken GitHub links. Commit new targets with the page;
CI validates again in a clean checkout.

## In-app offline manual

**Help → Documentation** renders the same Markdown locally in a searchable,
two-column reader. Vite includes the Markdown and `docs/assets/` images in the
frontend build, so desktop packaging needs no docs server or internet connection.
Internal page links and section anchors navigate within the reader. Source-code
and other external links are explicitly marked as online resources.

`frontend/src/shell/documentation.ts` defines the audience groups and excludes
`internal/` and assets' Markdown. Other included pages appear under Technical
reference. Its inventory test requires exact agreement with `mkdocs.yml` navigation;
corresponding `.zh-CN.md` pages are selected for Chinese UI. `DocumentationPanel` renders standard Markdown
and GFM tables, strips the home page's MkDocs layout wrappers and does not execute
HTML. Use relative Markdown image and page links for offline content.

Run the documentation and Help tests with
`npx vitest run src/shell/documentation.test.ts src/shell/DocumentationPanel.test.tsx src/shell/HelpMenu.test.tsx`
from `frontend`, then `npm run build` to verify that assets are packaged.

## Publish

This site tracks **`dev`**. It describes development behavior, which may be newer than an installer from Releases. The source/edit links and setup guide use that same branch. To switch publishing branches, update the workflow branch filters/conditions, `edit_uri`, the hook's source branch, and the setup guide together.

Enable **Settings → Pages → Build and deployment → Source → GitHub Actions** in the
repository. The [Documentation workflow](../../.github/workflows/docs.yml) checks
PRs to `dev`/`main` and pushes to `dev`, including code-only changes that could break
source links. The `build` job runs `scripts/docs.py check`. A separate tutorial job
installs locked backend test dependencies and the example entry points, then runs
the tutorial integration tests. Deployment requires both jobs to pass and only
runs on `dev` outside pull requests. Manual dispatch follows the same rule.

In **Settings → Environments → github-pages → Deployment branches and tags**, allow the `dev` branch. GitHub may initially allow only the default branch (`main`); in that case the build succeeds but the deployment is rejected before its steps run. Keep the environment policy aligned with the workflow's publishing branch.

The workflow builds a static site, uploads only `.tmp/docs-site`, then deploys to the `github-pages` environment with `pages: write` and `id-token: write`. See [GitHub's custom workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages) for repository prerequisites.

Expected address: <https://theafish.github.io/open-agent-world/>. A successful build alone does not prove deployment; check the deploy job and load the public URL.
