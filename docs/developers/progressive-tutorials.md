# Progressive card tutorials

[简体中文](progressive-tutorials.zh-CN.md)

Plugin API **1.26** lets card and Pack authors ship optional tutorials and offline reference documents through the ordinary catalog. No frontend module or tutorial-specific API route is required. Existing plugins remain compatible.

## User experience

Selecting, placing, expanding or inspecting a card offers its first available tutorial and those of its Pack. Opening a Pack also offers Pack tutorials. Loading an existing world or collection does not trigger tips. Only one tip appears at a time, and it does not take focus or block canvas actions. The user chooses **View tutorial**, reads the steps with **Continue**, or closes it with ×. Close remembers the current step and suppresses further automatic offers for that tutorial, including later revisions.

**Help → Card tutorials & docs** lists content from enabled, available card types and Packs, including dismissed tutorials. Users can resume, replay completed tutorials, read documentation, search by title/owner, or turn off all automatic hints. Unavailable plugin content leaves the list; its progress is retained for reinstallation.

Automatic encounters are ignored during the first-use walkthrough. Tips are hidden during dragging, connecting, and other dialogs; in a Legion Workspace the reader appears inside that dialog. Reading never creates cards, grants capabilities, starts tools, or sends model requests.

## Define a tutorial

```python
from open_agent_world.plugin_api import TutorialDefinition, TutorialStep

INTRO = TutorialDefinition(
    id="first-greeting",
    revision=1,
    title={"en": "Your first greeting", "zh-CN": "第一次问候"},
    summary="Personalize your Greeter in two steps.",
    steps=(
        TutorialStep(id="name", title="Choose a name",
                     body="Open the card and enter a name."),
        TutorialStep(id="greet", title="Say hello",
                     body="Click **Greet** to save your greeting."),
    ),
    document="## Greeter reference\n\nYou can change the name at any time.",
)
```

Pass `tutorials=(INTRO,)` to an existing `NodeTypeDefinition` for card-specific content, or to `PackDefinition` for Pack-wide content. Set the plugin descriptor and `.oawpack` manifest's `compatibility.plugin_api` to `1.26`. The complete standalone example is in `examples/packs/greeter`.

| Field | Contract |
| --- | --- |
| `id` | Stable, owner-local ID: lowercase letters, numbers, dots, underscores and hyphens; starts with a letter. |
| `revision` | Positive integer, default 1. Bump for a materially changed tutorial; completed old content can be offered again on encounter. Explicit dismissals remain respected. |
| `title`, `summary` | Plain text or locale map with required `en` fallback. |
| `steps` | Up to 50 steps with stable, unique `id`, localized `title` and Markdown `body`. Progress advances only when the user continues. |
| `document` | Optional inline Markdown, also localizable. Omit steps to publish a reference-only article; it appears in Help without an automatic tip. |
| `trigger` | `encounter` (default) or `manual` (Help only). |
| `after` | Tutorial IDs belonging to the same owner. Automatic offers wait until all are completed. Manual reading remains available. |

An owner is a card **type** or a Pack, never a placed card instance. The same local ID on two owners is independent. The registry rejects duplicate IDs, missing prerequisites, dependency cycles, empty content and invalid localized text before installing the plugin. Each owner supports at most 50 tutorials. A text value is limited to 100,000 characters.

A follow-up chapter uses the same interface:

```python
ADVANCED = TutorialDefinition(
    id="sharing", after=("first-greeting",),
    title="Share a greeting", summary="Use your card with a teammate.",
    steps=(TutorialStep(id="review", title="Review the greeting",
                        body="Check the saved name before sharing."),),
)
# NodeTypeDefinition(..., tutorials=(INTRO, ADVANCED))
```

Closing a prerequisite does not complete it. The next chapter stays available for manual reading, but is not automatically offered until the prerequisite is completed.

## Content Packs without code

Schema 2 Content Packs can include the same JSON definitions in `manifest.json` under `creator.tutorials`, and in each card recipe under top-level `tutorials` (alongside `id` and `design`). The host validates these definitions and publishes them with the Pack/card catalog. Use `compatibility.plugin_api: "1.26"`. Export requests also accept `creator.tutorials`.

```json
{
  "id": "intro",
  "title": "Using this card",
  "summary": "A short introduction.",
  "steps": [{"id": "open", "title": "Open the card", "body": "Enter your inputs, then click **Run**."}],
  "document": "## Reference\n\nDescribe the expected inputs and outputs."
}
```

Tutorial authoring currently uses Python or JSON; the visual Card Factory does not yet include a tutorial editor. Custom JavaScript predicates, automatic completion from tool output, arbitrary selectors, and guided mutations are intentionally outside this declarative contract. For a full application walkthrough, the existing first-use controller remains separate.

Markdown supports normal text, lists, code, tables and explicit HTTP(S) links opened in another tab. Raw HTML is skipped; image elements render their alternative text, and relative/executable links do not navigate. Inline text remains available offline; linked external sites require connectivity.

## Host architecture and persistence

- `backend/plugins/tutorials.py`: shared validation and public Python models.
- `frontend/src/tutorials/types.ts` and `catalog.ts`: data contract, owner identity, locale fallback.
- `store.ts`: reusable progression engine with injectable storage; no world or onboarding dependencies.
- `observe.ts`: adapter for successful placement, selection, surface changes, library inspection and Pack opening.
- `ProgressiveTutorials.tsx`: nonmodal reader and searchable Help library.

`oaw-progressive-tutorials-v1` stores only the enabled preference and each owner's tutorial ID, revision, status and step ID. It uses the existing backend-owned application profile, separate from world/card data and from the first-use tutorial. Content, credentials, selected card IDs and queues are not persisted. Reloading never resumes mutations; an unfinished tutorial can be offered on the next deliberate encounter. The development panel's tutorial reset also clears this record.

## Verification

```sh
python -m pytest backend/tests/test_tutorials.py backend/tests/test_content_packs.py
cd frontend
npx vitest run src/tutorials src/shell/HelpMenu.test.tsx
node scripts/run-e2e.mjs e2e/progressive-tutorials.spec.ts
```
