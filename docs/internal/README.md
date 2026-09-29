# Maintainer records

This directory contains proposals, dated investigations, and acceptance records.
It is excluded from both the documentation site and the bundled offline manual.
Use [the published documentation](../README.md) for maintained instructions.

| Kind | Records |
| --- | --- |
| Design explorations | [Card collection](design/shadow-collection.md), [shadow boundary](design/shadow-gas-boundary.md), [reader transition](design/reader-transition.md) |
| Plans and operator follow-up | [MatCreator demo](plans/matcreator-demo.md), [Marketplace production](plans/marketplace-production.md) |
| Acceptance snapshots | [Pack Store](validation/pack-store.md), [enterprise foundations](validation/enterprise-foundations.md) |
| Performance investigations | [Legion](performance/legion.zh-CN.md), [pan/zoom](performance/panzoom.zh-CN.md), [card rendering](performance/card-rendering.zh-CN.md), [wide viewport](performance/wide-viewport.zh-CN.md), [1,000-card wheel regression](performance/stress-zoom.md), [terrain measurements](performance/terrain-webgl.md) |

The [canvas investigation history](performance/canvas.md) preserves earlier drag,
Deck, and SVG measurements; the maintained [profiling guide](../canvas-performance.md)
points to current tools.

These records preserve the language and validation scope of the original work.
Chinese investigations use `.zh-CN.md`; a translation is not required for a
historical record. Local artifact paths document where evidence was captured;
they do not promise that the artifact exists in another checkout.

For a new record, include its date/revision, status (proposal, investigation, or
validation), reproduction conditions, observed results, and unverified scope.
Move lasting behavior and contracts into the relevant published reference page,
then link the record as historical evidence. Do not present a passing historical
test count as current acceptance.
