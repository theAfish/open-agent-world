# Minister

Minister is a builtin local canvas administrator. Open **Core essentials** in the
Card Library, add Minister to your deck and place it on the canvas. Click its
circular node to talk beside it. Choose a configured model under **Model & actions**.

New Ministers have **Allow canvas edits** enabled. Turn it off to pause administration;
existing explicitly disabled Ministers stay disabled. New Ministers have a radius of 1200 canvas units. The area preview appears only while hovering over the Minister. Open settings to drag the radius handle or
enter a radius from 200 to 3000. The circle follows the node, and every affected
card's saved rectangle must fit inside it. Glued surface rectangles are checked too.
Only the user can move a Minister or change its control radius/authority.

**Nearby cards** searches names, types and IDs. Inspection includes the controller
identity, positions, connections, container membership, glue, public configuration,
and available card/relationship policies. Private Minister control chats and raw
secrets are excluded. Pair inspection explains supported relationships before a call.

## Local administration

Minister can create normal cards, including Agents; move, rename and resize cards;
change normal configuration; connect/disconnect supported relationships; group
root cards into a Legion; change container membership; attach/detach equipment;
and glue/unglue root card surfaces. A card-update batch uses the existing atomic
service operation for layout or configuration changes.

Grouping uses existing Legion geometry, so the whole resulting container must fit
inside the circle. Ungrouping detaches selected members and preserves their cards
and container. Deleting the empty container is a separate reviewed action. Glue
is a physical canvas bond, distinct from capability connections and owned equipment.
Its existing surface/bond data now uses shared revisioned state; browser gestures
and background actors see the same layout. Moving a glued card also moves peers.

The host decides each mutation's risk before lifecycle effects or persistence:

| Decision | Behavior |
| --- | --- |
| **ALLOW** | Normal local creation, structure and public writable configuration execute directly. |
| **CONFIRM** | Deletion, sensitive settings, unfamiliar plugin initialization and dangerous grants produce a proposal in the Minister panel. Nothing is applied until the user confirms. |
| **DENY** | Raw secrets, immutable/internal fields, boundary bypasses and changes to Minister authority cannot be approved through a Minister tool. |

Reviews list affected cards, connections, access changes and relevant resources/runs.
Sandbox host folder, network and runtime changes are confirmable. Execution,
environment/credential access and other sensitive relationships are confirmable;
their existing validators and lifecycle rules still apply. Connecting an ordinary
Agent to a Text or Conversation is normal local administration. Connecting Minister
itself is supported for its approved relationships and never expands its intrinsic tools.

Approval is a desktop management action, absent from Agent tools. It is bound to
the exact proposed request and effects; scope, object incarnations/revisions and
effects are rechecked under the existing mutation barrier. A stale/revoked proposal
must be inspected and proposed again. Reviews expire after 15 minutes or on backend
restart, and can only be decided once. They are not durable grants or general undo.
After a successful confirmation, the panel resumes the existing conversation so
Minister can inspect the result and continue. Later sensitive actions still need review.

## Chat setup and completion

For “set up a place where I can chat with an agent”, Minister should make a short
goal/requirements plan, then provide a visible Conversation, an ordinary Agent
(create one if needed using the host's default model), a participate connection,
General membership and sensible spacing. A Text card or the private Minister
control chat does not complete that request. Minister itself participates when
the user asks to chat with Minister.

After changes, inspect completion criteria. Chat readiness distinguishes configured
message routing from an observed reply. An untested model reply must not be described
as verified. Reuse partial setups and preserve the goal across “try now”.

## Current implementation boundaries

The tools administer saved card configuration and structure. They do not yet edit
arbitrary plugin document bodies, enter credential values, start arbitrary runtime
actions, import blueprints or offer general undo. These are implementation limits,
not product rules against Ministers administering special card types. Secret entry
continues through the existing host credential UI. Creation of managed collection
members continues to require that collection's dedicated operation.

The Minister harness and tools add a small risk policy and review queue around
ApplicationServices.canvas_control. The facade validates scope, schemas,
consequences and revisions; existing services own mutations and lifecycle
compensation. Plugins can mark ordinary creation/relationships as not requiring
confirmation; unreviewed effects default to confirmation. Existing generic
automation grants keep their original config restrictions.

Tests exercise real tools, persistence, lifecycle and events, including concurrent
Minister edits, human conflicts, confirmed/rejected/stale proposals, grouping,
equipment, shared glue, sensitive grants, and the supplied Chinese chat scenario.
Run npm run test:e2e:minister from frontend for isolated browser checks. Its
deterministic provider invokes real tools; these tests do not prove a production
language model's planning quality or contact a model service.
