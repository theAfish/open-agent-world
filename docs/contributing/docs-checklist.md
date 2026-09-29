# Documentation writing checklist

**English** | [简体中文](docs-checklist.zh-CN.md)

Use this checklist before opening a documentation PR. The
[site maintenance guide](../developers/documentation.md) covers installation,
preview, validation, and publication.

## 1. Choose the reader and page type

| Reader / purpose | New page location | Required content |
| --- | --- | --- |
| Everyday user completing a task | `docs/user-guide/` | Goal, prerequisites, steps, visible result, recovery, next task |
| Plugin author learning a capability | `docs/developers/` | Goal, working example, verification, next lesson, contract links |
| Developer looking up behavior | `docs/reference/` | Scope, ownership, contract, limits, related references |
| Host or documentation contributor | `docs/contributing/` | Workflow, checks, expected results, next step |
| Maintainer recording a proposal or investigation | `docs/internal/design/`, `plans/`, `performance/`, or `validation/` | Date/revision, status, evidence, limitations |

Existing root-level reference URLs remain valid. Improve their content in place;
put new reference pages in `reference/`. Package-specific usage stays beside the
package and is linked from a guide. The root README is a portal, not a second manual.

## 2. Keep one prose language per page

- Write the canonical page in English as `topic.md`. Put Chinese content in
  `topic.zh-CN.md` beside it; never append a Chinese section to an English guide.
- Link both versions near the top when both exist. A Chinese-only example is
  allowed with the language suffix and a Chinese navigation entry.
- Keep identifiers, commands, API names, and exact UI labels unchanged. Explain
  them in the page's prose language. Do not translate executable identifiers.
- In a Chinese page, label links to English-only instructions with “English” or
  the equivalent Chinese label. Do not imply that a partial translation is complete.
- Update both versions for behavior changes, or clearly identify the translation's
  narrower scope and link the maintained source. Language checks cannot verify
  translation accuracy; this remains part of review.

## 3. Give the page a reading sequence

Use one `#` title, then `##` sections and `###` subsections without skipping levels.
Begin with who the page is for and what it enables. State prerequisites before
steps; put the expected result after commands. Finish with specific next reading.

Tutorials teach a path, how-to guides complete a task, and references define
contracts. Link between them instead of repeating long explanations. Keep dated
benchmarks and one-off test counts in `internal/`.

## 4. Use portable links and examples

- Link Markdown pages and images with relative paths. Check filename case; Linux
  CI is case-sensitive even when a local Windows checkout is not.
- Use repository-relative links to source files. The site converts these to GitHub
  links; the offline reader marks them as online resources.
- Do not link ignored local outputs such as `.tmp/` or `.outputs/`. Describe a local
  artifact path as inline code in a maintainer record, or commit a suitable asset.
- Give code fences a language. State the working directory and distinguish
  PowerShell from shell commands when syntax differs.
- Prefer normal Markdown that also works in the offline reader. Site layout HTML
  is reserved for the landing page.

## 5. Register and check

Add every published page to `mkdocs.yml` in its audience section. Chinese pages
belong in the Chinese section. `internal/` and the asset README are excluded;
do not add individual exclusions to hide an unfinished public page.

Run the same command as CI, using the documentation environment's Python:

```sh
python scripts/docs.py check
```

It checks inventory, headings, language separation, repository links, the strict
MkDocs build, generated links/anchors/assets, and search boundaries. For changes
to the offline manual or its inventory, also run the focused Help tests and
frontend build listed in the [maintenance guide](../developers/documentation.md#in-app-offline-manual).

The PR should say which reader benefits, what changed, and which checks ran.
Theme or layout changes also need visual verification at desktop/mobile widths
in light and dark themes. A local build does not establish a successful Pages deployment.
