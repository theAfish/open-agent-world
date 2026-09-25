# Interactive tutorial

**English** | [简体中文](tutorial.zh-CN.md)

An empty world opens with the OAW logo and a choice of starting blueprints: **General assistant**, **Coding workspace**, and **Multi-Agent collaboration**. Each places its connected cards directly on the canvas without a Legion wrapper, using the [Legion template system](legions.md). Saved Legions are also available as starting blueprints. **Start Tutorial** begins the guided walkthrough, while **Start Empty** adds nothing. Missing model setup opens the existing Settings flow with a small resumable guide; no Minister is appointed automatically. The tutorial takes place on the real canvas. It covers navigation, cards, an Agent–Conversation–Sandbox workflow, sticking cards together, promotion of the existing Agent into a Minister, and finally forming a Legion and arranging its Workspace. The compass button in the world controls replays it.

The Minister chapter displays `Agent + Minister role = Minister Agent`. It adds the real Minister role card to the active deck, then guides the user to place it beside their Agent and drag it onto that Agent. The role card is absorbed as the Agent is promoted and contracts to a circular node with a small crown. The user then explores canvas chat and the Minister tab inside the Agent card, where permissions and confirmations live. History remains in the existing Agent workspace. Progress observes the persisted role and actual interactions. Users who skip the tutorial can find the same role card in the Core essentials pack and add it to any deck.

The guide waits for your actions. Its small compass button finds the current subject or recovers a missing card. Minimize the bubble to clear some space; × skips the tutorial. After a reload, choose Resume or restart. Model setup uses the ordinary Agent settings and **Manage models**. Sending to a model is optional; a real provider must be configured to receive a useful reply. The guide does not generate a simulated response or start a Sandbox for you.

Deck preparation is hands-on: the guide points to the Library, the starter pack, and the opened wrapper. Drag Text, Agent, Conversation, and Sandbox into one chosen deck in the collection sidebar, then activate it to return to the canvas. Clicking Add to opens the same destination choice. The selected deck must contain all four available cards before continuing.

Your placed cards and collected deck entries stay in your world. The guide registers its temporary Text cards and removes unchanged props on finish, skip, or restart. If you edit a prop, connect it, or stick it to your own card, it is kept. Failed cleanup retains its ledger and offers a retry.

## Legion and Workspace

The final chapter brings your existing Agent, Conversation and Sandbox together. Hold **Ctrl** (**⌘** on Mac) and click the three cards, then choose **Form Legion**. The guide recognizes the saved membership and retains existing connections. A Legion starts as a group; team mode is an optional setting for shared instructions and state.

Open **Workspace mode** in the Legion header. Place Conversation first, dock Sandbox beside it, and choose **Done editing**. Dragging and the ordinary selection/docking buttons both work. Agent can remain unplaced. The guide waits for the saved layout, then introduces split panels, tabs and individual card sections before **Back to canvas**. Your Legion and layout survive tutorial cleanup and can later be saved to the library as a reusable setup.

Guidance renders inside the native Workspace dialog, with space reserved beside or below the panels. Pause, resume, skip and recovery remain accessible. After a reload, resume and reopen Workspace mode; a missing Legion returns recovery to grouping the surviving cards.

## Implementation

- `frontend/src/onboarding/steps.ts` is the chapter sequence and pure completion predicates. Each step has a stable ID, short dialogue, a subject, an optional demonstration, and an expected action or manual continuation. Add chapters here and implement any new predicate/action explicitly; there is no workflow language or plugin SDK.
- `controller.ts` owns progress, API orchestration, recovery, and the temporary-card ledger. It subscribes to `worldStore`, `nodeSurfaces`, and `cardLibrary`. Only public IDs, content revisions, names, and progress are persisted in `oaw-onboarding-v1`; no message contents, model credentials, or world copies are saved in the tutorial state. Persistence is browser-origin scoped, matching the current single-world frontend. A future workspace switcher should give this record an authoritative world identity.
- `state/interactions.ts` carries small successful UI signals where snapshots cannot identify the actor: user viewport changes, focus, confirmed message sends, Minister presence, and successfully saved glue. The ordinary interaction handlers emit these signals. There is no tutorial-specific implementation of card gestures or graph permissions. Failed sends and optimistic glue changes cannot complete a step.
- `Onboarding.tsx` positions the guide beside the current subject, highlights it, and supplies cancellable visual demonstrations. `placement.ts` keeps bubbles clear of controls and finds open space for props and the Minister without moving existing cards. Real card placement, position commits, relationships, and glue still use the existing library, world, and shared glue APIs. Normal capability selection, deletion/undo, and Minister confirmations retain their existing behavior.
- Placement stages the real node before its first paint and camera framing. A single inert copy flies in screen coordinates while the real node, guide anchor, and spotlight keep their final bounds; completion reveals the node and removes the copy before another paint. Cancellation always clears staging. Library hints render inside the modal so they remain interactive in the browser top layer.
- `OawGuide.tsx` keeps the adopted `docs/assets/logo.svg` intact for the welcome pose. During entrance, its single circular head travels from the profile position to the standing character while the body turns into the symmetric front silhouette. The standing proportions and motion study come from the `.tmp` reference, retaining the logo's circular head, navy color, tapered body, and two legs; no arms or extra anatomy are added.
- `guideRig.ts` implements the standing character as a vector bone rig. Each leg has a hip, knee, and foot chain solved with two-bone IK; weighted cubic boundary controls bind its silhouette to the pelvis, thigh, and shin. Idle, Walk, Jump, Think, Indicate, Speak, and Enter share the same skeleton. The intent machine retains pose and velocity across changes, and a one-shot jump returns to the latest intent. The host's measured screen movement drives gait speed and direction; successful tutorial actions trigger jumps. Reduced motion holds the neutral standing pose. A single animation clock updates SVG geometry directly, without React renders per frame or CSS clip restarts. `debug` reveals the bones for development previews.

Eligibility uses a full authoritative world snapshot, so an empty viewport in a populated world does not trigger onboarding. A pending demonstration is cancelled and drained before its registered props are cleaned up. A reload pauses rather than running mutations in the background. Recovery never treats a missing viewport card as a successful deletion; deletion requires the existing history or runtime deletion evidence.

## Verification

From `frontend`, run:

```sh
npx vitest run src/onboarding src/state/interactions.test.ts
node scripts/run-e2e.mjs e2e/onboarding.spec.ts --grep-invert no-preference
node scripts/run-e2e.mjs e2e/onboarding.spec.ts --grep no-preference
node scripts/run-e2e.mjs e2e/onboarding-legion.spec.ts e2e/guide-rig.spec.ts
```

The browser suite checks welcome choices, a smaller viewport, reload/resume, and the full tutorial with normal and reduced motion. It runs against the existing isolated backend, performs actual gestures, grants real relationships, and checks that the workflow survives prop cleanup. The isolated backend uses its test runtime; these tests do not establish live model quality or native Sandbox execution.

### Guided interaction scope

While a step is active, `interactionGuard.ts` accepts input only for that step's subjects and required controls. This is separate from spotlight shading: card placement leaves the canvas visible, but only the requested deck card can begin a placement and the canvas accepts its drop. Connections allow both endpoints and their capability chooser. Model steps allow their current form section, with the settings close/reopen path retained.

Global shortcuts are blocked except the step's focus/delete action on its own selected card; editing shortcuts still work inside allowed fields. Tab stays within allowed controls. Accepted gestures can finish across step transitions. Pause (or Escape) releases the scope without discarding progress; Resume rebases the current step, and Skip retains the existing cleanup/recovery flow. Add or change an interaction scope alongside any new tutorial step, and verify its complete source-to-destination gesture.

### Guide travel

`guideTravel.ts` keeps near movement on the existing walking path and uses a 720 ms entry/exit portal for trips beyond 480 screen pixels. Destination changes during transit do not restart its clock. The bubble keeps its layout measurements but becomes hidden, inert, and hidden from accessibility APIs while the guide moves; it reappears on arrival. Pause and reduced-motion preferences finish travel immediately. Portal styling uses the active theme, and the character still uses the shared rig.
