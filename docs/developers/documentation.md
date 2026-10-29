# Maintain the documentation

Follow the [writing checklist](../contributing/docs-checklist.md). The site and
in-app manual read the same Markdown under `docs/`; plugin READMEs document their
own packages. `docs/internal/` is excluded from both public readers.

## Preview and check

From the repository root, use a Python environment with:

```sh
python -m pip install -r scripts/docs-requirements.txt
python scripts/docs.py serve
python scripts/docs.py check
```

`check` validates the published inventory, headings/language, repository links,
strict MkDocs build, generated anchors and search output. It needs no backend.

After changing navigation, links or Help behavior, run from `frontend`:

```sh
npx vitest run src/shell/documentation.test.ts src/shell/DocumentationPanel.test.tsx src/shell/HelpMenu.test.tsx
npm run build
```

The existing browser scenario is `e2e/documentation.spec.ts`; it checks offline
reading, modal focus, images, language switching and small layouts. Use
`node scripts/run-e2e.mjs e2e/documentation.spec.ts` after preparing the test host.

## Navigation and links

List each **topic** once in `docs/documentation.json` under its reader group,
using the default English filename. MkDocs inherits this file through `INHERIT`;
the application imports the same navigation and language list. Do not add a
separate navigation entry for each translation.

Keep translations next to their source: `guide.md`, `guide.zh-CN.md`,
`guide.ja.md`. The filename without a locale is the default English source and
must exist for every topic. Locale tags identify languages, not flags or countries.
The published English URL stays `/guide/`; translations use `/zh-CN/guide/`.
Existing suffixed Chinese URLs redirect to the new URLs, preserving fragments.

The website header and in-app documentation toolbar provide language selection.
Do not add language-switch links below article titles. In the application,
reading language defaults to the application language; an explicit choice is
saved separately per workspace. Missing translations display the default source
with a notice, keeping the selected reading language for subsequent topics.

## Add or update a translation

For an existing language, add a sibling such as `user-guide/models.ja.md`.
For a new language, first add an entry to `plugins.i18n.languages` in
`docs/documentation.json` by copying a non-default entry:

1. Set `locale` (for example `ja`), its native `name` (`日本語`), `default: false`
   and `build: true`. Set `theme.language` to Material's supported language code.
2. Translate the four `extra.documentation` messages and the four audience labels
   in `nav_translations`. Preserve message placeholders such as `{available}`.
3. Start with the landing page, user entry and Card App tutorial. Partial
   translations are allowed; do not copy English into a locale file merely to
   hide the fallback notice. For a language written right to left, set
   `theme.direction: rtl`; the application uses automatic article direction.
4. Run the checks above and review both the website and in-app reader. The plugin
   adds supported search languages automatically; for regional codes, check
   Lunr's supported code and add it to `plugins.search.lang` if needed.

Keep commands, API identifiers and visible UI names accurate. When changing
product facts, update existing translations or include the affected files in the
review. File presence does not establish translation freshness. This repository
does not automatically translate or certify translations.

## Links and publication

Link to a topic's canonical filename, such as `../install.md`, without a locale
suffix; readers resolve it to their selected language. For a specific section,
use the source filename and its actual anchor. Section links open that source
rather than guessing a translated heading.
Use relative Markdown links for tracked source files. MkDocs sends
source links to GitHub `dev`; the in-app resolver keeps pages/assets local and
marks repository resources as online. New pages and assets are bundled at build
time, so a website edit does not update an already installed application's manual.

Keep stable old entry URLs when they have useful inbound links. A short pointer
is enough; do not keep a second tutorial. Never describe ignored local logs as
portable evidence.

The docs workflow checks PRs and branch pushes. Pages publication on `dev` is
separate from validation and from desktop releases; inspect the workflow result
when checking whether a website update is live. This repository's site follows
dev, while release users should consult their bundled manual and release notes.
