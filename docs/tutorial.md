# Interactive tutorial

An empty world opens with the OAW logo and three choices: **Start Tutorial**, **Place Minister Card**, and **Start Directly**. The tutorial takes place on the real canvas. It covers navigation, cards, an Agent–Conversation–Sandbox workflow, sticking cards together, and the Minister. The compass button in the world controls replays it.

The guide waits for your actions. Its small compass button finds the current subject or recovers a missing card. Minimize the bubble to clear some space; × skips the tutorial. After a reload, choose Resume or restart. Model setup uses the ordinary Agent settings and **Manage models**. Sending to a model is optional; a real provider must be configured to receive a useful reply. The guide does not generate a simulated response or start a Sandbox for you.

Your placed cards and collected deck entries stay in your world. The guide registers its temporary Text cards and removes unchanged props on finish, skip, or restart. If you edit a prop, connect it, or stick it to your own card, it is kept. Failed cleanup retains its ledger and offers a retry.

## Implementation

- `frontend/src/onboarding/steps.ts` is the chapter sequence and pure completion predicates. Each step has a stable ID, short dialogue, a subject, an optional demonstration, and an expected action or manual continuation. Add chapters here and implement any new predicate/action explicitly; there is no workflow language or plugin SDK.
- `controller.ts` owns progress, API orchestration, recovery, and the temporary-card ledger. It subscribes to `worldStore` and `nodeSurfaces`. Only public IDs, content revisions, names, and progress are persisted in `oaw-onboarding-v1`; no message contents, model credentials, or world copies are saved in the tutorial state. Persistence is browser-origin scoped, matching the current single-world frontend. A future workspace switcher should give this record an authoritative world identity.
- `state/interactions.ts` carries small successful UI signals where snapshots cannot identify the actor: user viewport changes, focus, confirmed message sends, Minister presence, and successfully saved glue. The ordinary interaction handlers emit these signals. There is no tutorial-specific implementation of card gestures or graph permissions. Failed sends and optimistic glue changes cannot complete a step.
- `Onboarding.tsx` positions the guide beside the current subject, highlights it, and supplies cancellable visual demonstrations. `placement.ts` keeps bubbles clear of controls and finds open space for props and the Minister without moving existing cards. Real card placement, position commits, relationships, and glue still use the existing library, world, and shared glue APIs. Normal capability selection, deletion/undo, and Minister confirmations retain their existing behavior.
- `OawGuide.tsx` keeps the adopted `docs/assets/logo.svg` intact for the welcome pose. During entrance, its single circular head travels from the profile position to the standing character while the body turns into the symmetric front silhouette. The standing proportions and motion study come from the `.tmp` reference, retaining the logo's circular head, navy color, tapered body, and two legs; no arms or extra anatomy are added.
- `guideRig.ts` implements the standing character as a vector bone rig. Each leg has a hip, knee, and foot chain solved with two-bone IK; weighted cubic boundary controls bind its silhouette to the pelvis, thigh, and shin. Idle, Walk, Jump, Think, Indicate, Speak, and Enter share the same skeleton. The intent machine retains pose and velocity across changes, and a one-shot jump returns to the latest intent. The host's measured screen movement drives gait speed and direction; successful tutorial actions trigger jumps. Reduced motion holds the neutral standing pose. A single animation clock updates SVG geometry directly, without React renders per frame or CSS clip restarts. `debug` reveals the bones for development previews.

Eligibility uses a full authoritative world snapshot, so an empty viewport in a populated world does not trigger onboarding. A pending demonstration is cancelled and drained before its registered props are cleaned up. A reload pauses rather than running mutations in the background. Recovery never treats a missing viewport card as a successful deletion; deletion requires the existing history or runtime deletion evidence.

## Verification

From `frontend`, run:

```sh
npx vitest run src/onboarding src/state/interactions.test.ts
node scripts/run-e2e.mjs e2e/onboarding.spec.ts e2e/guide-rig.spec.ts
```

The browser suite checks welcome choices, a smaller viewport, reload/resume, and the full tutorial with normal and reduced motion. It runs against the existing isolated backend, performs actual gestures, grants real relationships, and checks that the workflow survives prop cleanup. The isolated backend uses its test runtime; these tests do not establish live model quality or native Sandbox execution.
