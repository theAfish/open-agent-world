# Maintain this documentation site

The site uses Material for MkDocs and GitHub Pages. Markdown in `docs/` remains the source of truth; plugin-specific guides stay beside their packages. There is no second Wiki to synchronize.

## Preview locally

From the repository root, install the documentation tools in a separate environment:

```sh
uv venv .tmp/docs-venv
```

Windows:

```powershell
uv pip install --python .tmp/docs-venv/Scripts/python.exe -r scripts/docs-requirements.txt
./.tmp/docs-venv/Scripts/python.exe -m mkdocs serve
```

Linux/macOS:

```sh
uv pip install --python .tmp/docs-venv/bin/python -r scripts/docs-requirements.txt
.tmp/docs-venv/bin/python -m mkdocs serve
```

The preview prints its local URL. To build with link and navigation validation, replace `serve` with `build --strict`. Output goes to `.tmp/docs-site` and is not committed. Run `scripts/docs_tests.py` and `scripts/docs_check_site.py` with the same environment's Python for source-link tests and a full generated-link/search check.

## Put content in the right place

| Audience | Location | Writing rule |
| --- | --- | --- |
| Everyday users | `docs/user-guide/` | Actions, visible results, and recovery; no implementation walkthroughs |
| New plugin authors | `docs/developers/` | Ordered lessons with runnable examples and expected results |
| Experienced developers | Technical reference navigation | Contracts, lifecycle, architecture, and detailed behavior |
| Maintainers planning future work | Repository-only design notes | Label as proposals; exclude from the site |

Keep English as the primary language. Link Chinese entry pages explicitly and label English-only destinations. Avoid sending a new user from an ordinary task straight into an API contract.

Add published pages to `mkdocs.yml`. Unlisted pages and broken local links fail the strict build. Excluded planning notes stay out of generated HTML and search. General search covers landing pages, installation, user guides, and guided developer pages; detailed contracts remain accessible through Technical reference and the extension-point directory. This keeps implementation details out of ordinary search results.

`scripts/docs_hooks.py` converts repository source links and excluded-note links to GitHub while leaving normal documentation links for MkDocs to validate. The GitHub target must exist in the checkout or the build fails.

## Publish

This site tracks **`dev`**. It describes development behavior, which may be newer than an installer from Releases. The source/edit links and setup guide use that same branch. To switch publishing branches, update the workflow branch filters/conditions, `edit_uri`, the hook's source branch, and the setup guide together.

Enable **Settings → Pages → Build and deployment → Source → GitHub Actions** in the repository. The [Documentation workflow](../../.github/workflows/docs.yml) validates pull requests and builds/publishes documentation changes pushed to `dev`. It also tests the tutorial plugin packages. Manual dispatch becomes available once the workflow exists on the repository's default branch; it only deploys when run on `dev`.

In **Settings → Environments → github-pages → Deployment branches and tags**, allow the `dev` branch. GitHub may initially allow only the default branch (`main`); in that case the build succeeds but the deployment is rejected before its steps run. Keep the environment policy aligned with the workflow's publishing branch.

The workflow builds a static site, uploads only `.tmp/docs-site`, then deploys to the `github-pages` environment with `pages: write` and `id-token: write`. See [GitHub's custom workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages) for repository prerequisites.

Expected address: <https://theafish.github.io/open-agent-world/>. A successful build alone does not prove deployment; check the deploy job and load the public URL.
