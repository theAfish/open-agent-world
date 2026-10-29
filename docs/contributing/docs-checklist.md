# Documentation checklist

1. Choose one reader and one task. Put steps in a guide, exact contracts in
   reference, and host operation in the maintainer path.
2. Verify product facts against current code, tests and examples. Use visible UI
   labels. Mark experimental features and distinguish dev from released builds.
3. State prerequisites, commands, expected results and the next useful step.
   Prefer one complete example over disconnected snippets.
4. Link the authoritative explanation instead of repeating it. Preserve useful
   old paths and anchors when moving content. Use repository-relative links
   with exact filename case; do not link ignored local outputs.
5. Keep translations consistent with current product facts. Use `.md` for the
   default English source and `.<locale>.md` for translations. Partial translation
   is allowed; prioritize continuous user and developer entry paths.
6. Register each topic once in `docs/documentation.json`. Keep language switches
   in the reader toolbar, not the article. Follow the [translation and checking guide](../developers/documentation.md).

Do not put dated test counts, PR summaries, migration diaries or future roadmaps
in maintained instructions. Keep only historical evidence with continuing value
in `docs/internal/`, explicitly non-authoritative; otherwise rely on Git history.
