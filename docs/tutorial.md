# Maintain the interactive tutorial

The welcome screen offers **Start Tutorial**, **Choose a workspace** and
**Start blank**. User instructions live in [Your first team](user-guide/first-team.md).
The guided canvas tutorial additionally covers Sandbox, glue, Minister and Legion
workspace layout. Model replies are optional and require a configured service;
the tutorial does not start a Sandbox automatically.

## Edit the existing tutorial

| Source | Responsibility |
| --- | --- |
| [steps.ts](../frontend/src/onboarding/steps.ts) | Stable step IDs, dialogue, targets and completion predicates |
| [controller.ts](../frontend/src/onboarding/controller.ts) | Persisted progress, demonstrations, recovery and temporary props |
| [Onboarding.tsx](../frontend/src/onboarding/Onboarding.tsx) | Welcome choices, guide placement and controls |
| [interactionGuard.ts](../frontend/src/onboarding/interactionGuard.ts) | Allowed gestures and controls during a step |
| [messages.json](../frontend/src/i18n/messages.json) | English/Chinese UI text |

Use ordinary host interactions and permission selection. Do not add tutorial-only
card behavior. Keep step IDs stable; changing dialogue should not reset progress.
A demonstration must be cancellable and register its temporary props. Cleanup
must preserve user edits and connected user objects. Reload pauses mutations;
recovery must inspect authoritative state rather than infer deletion from visibility.

Test the complete gesture when changing a step, including source and destination,
pause/resume and reduced motion. Run the targeted onboarding Vitest files and
`e2e/onboarding.spec.ts` / `e2e/onboarding-legion.spec.ts` with the existing runner.
These browser checks do not verify real models or native Sandbox isolation.

## Card-owned guidance

Installed cards can supply their own [progressive tutorials](developers/progressive-tutorials.md).
They appear through **Help → Card tutorials & docs** and use the same host engine.
Keep action names aligned with the card's real UI; do not claim a manual Continue
step observes or executes the operation for the user.
