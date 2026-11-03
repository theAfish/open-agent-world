# Mobile layout and touch conventions

Mobile interactions should support a continuous, deliberate gesture. A menu is
useful as an alternative entry, but is not a replacement for designing touch.

## Layout and input

- `interaction/environment.ts` runs once at application startup, including sign-in
  and published applications. Its Zustand store is transient, not profile data.
- `compact` means a layout viewport at most 700 CSS pixels wide. It is independent
  of touch capability. Existing component container queries remain in use.
- `touch` reflects coarse-pointer hardware, touch points, or an observed touch/pen.
  Using a keyboard does not shrink controls on a touch-capable device.
- `input` records the current mouse, touch, pen or keyboard interaction. Do not use
  user-agent sniffing to select layouts or gestures.
- `--app-height`, `--app-top`, `--app-bottom` describe the visible viewport at
  normal browser scale, including keyboard-induced resize/scroll. During browser
  pinch zoom they keep layout geometry stable. Browser page zoom stays enabled.
- `--safe-*` and `--screen-*` expose system insets and screen gutters. Use them for
  viewport overlays; nested panes should use their own container dimensions.

## Targets and feedback

- Shared screen-space controls use a 44px minimum touch target, with 8px spacing
  when arranging new controls. Icons need not themselves become larger.
- The foundation deliberately excludes transformed canvas content from automatic
  target growth. A CSS-pixel target inside a zoomed card is not a screen target;
  full-screen card presentation and canvas gesture arbitration need their own work.
- A control shows brief visual press feedback. A recognized hold shows a persistent
  accent outline and one brief highlight, without changing hit-test geometry.
- `hapticFeedback('selection' | 'hold' | 'confirm')` emits a short optional pulse.
  Shared `IconButton` touch activations use selection feedback. Call confirmation
  feedback only after the relevant action succeeds, never merely on drop/submit.
- No workflow depends on hardware vibration. Unsupported/blocked vibration is a
  no-op. Reduced-motion mode keeps the hold outline but disables animation and
  vibration. Do not vibrate continuously while dragging or hovering a target.

## Hold followed by drag

`useTouchHold` is opt-in. `canvas/useTouchCardGestures.ts` applies one recognizer at
the world boundary. A card crown/grip hold picks it up; a body hold reuses React
Flow's connection renderer, relationship validation and capability chooser.

1. Touch down on an explicit non-text surface. A short tap keeps its normal action.
2. Hold for 450ms within a 10 CSS pixel radius. Movement before recognition cancels
   the hold and leaves scrolling/panning to the owning surface.
3. On recognition, show `data-touch-hold="active"`, acknowledge once, and capture
   the same pointer. `onMove` continues the gesture; `onRelease` completes it.
4. A second finger, pointer cancellation, lost capture, window blur, hidden page,
   Escape, disabling or unmounting cancels ownership and must clear any preview.
5. Consume the click generated after a recognized hold. Keyboard activation remains
   available; mouse and pen keep their existing interactions.

Spread `bind` onto the gesture surface and render `data-touch-hold={phase}`. Keep
editable/selectable text and child controls out of that surface. The consumer must
choose its `touch-action` and arbitrate with React Flow before using the recognizer
on a card; do not globally disable selection, scrolling, context menus or zoom.

Compact browsing disables immediate node dragging, preserving native canvas pan
and pinch. Wide layouts retain mouse card dragging. A 44 screen-pixel crown grip
stays usable at any canvas zoom, including on a compact mouse window. Holding the
crown acknowledges once, raises the card's shadow and captures that same pointer.
Movement reuses the existing drag/drop, glue and position-commit callbacks. Only
release commits; second-finger takeover, cancellation, Escape or blur restores the
saved geometry. An unmoved hold never writes a position. There is no move mode.
Recognized card gestures block native camera movement until release or cancellation.
Invalid/duplicate targets
cannot open the capability chooser or create an edge. Holding a connection near
the screen edge pans the camera; release/cancellation stops that navigation.

## Screen-space mobile flows

- The compact world menu collapses its controls and offers a workspace list, so
  opening a workspace does not require finding a small canvas header.
- A single horizontal navigation row contains zoom, fit and the canvas tools
  entry. Pins, glue and the optional minimap live behind that entry. The
  development badge has its own 44px slot beside World and never covers it.
- Compact layouts disable camera double-click/double-tap zoom: rapid card
  reopening must not compete with detail activation. Pinch and zoom buttons stay.
- Card inspector/workspace content moves through stable portal hosts into a
  full-screen native dialog. Its React owner and drafts stay with the card. Only
  the transient mobile surface changes; saved desktop surface levels/sizes stay.
- Compact composed workspaces flatten their layout into a view picker and one
  full-width pane. Browsing never writes desktop split ratios or tab selection.
  Published access still filters permitted sections and excludes unplaced cards.
- Compact decks use a single 44px tab row, even with a mouse in device simulation.
  They expand explicitly, without hover. Touch decks have a horizontal scrolling row,
  tap placement and deliberate upward-drag placement. Busy deck mutations disable
  card actions. Swiping or adding another finger cannot accidentally place a card.
- Shared nested maps support midpoint-anchored pinch navigation. Cancellation and
  second-finger takeover roll back incomplete node moves without persisting them.
- Visualization charts support touch pan/pinch along their existing horizontal
  domain, retain the fixed Y scale, and inspect a point on tap.

## Verification

Targeted unit tests cover viewport state, graceful haptics and gesture lifecycle.
`interaction-foundation.spec.ts` uses a small real-browser fixture for CSS sizing,
rotation and native touch capture. It needs a Vite server, but no backend or stored
user data. These tests do not establish full application or real-device support.
Additional isolated-backend browser tests cover native hold connections, duplicate
rejection, pan/pinch/move arbitration, full-screen drafts, compact workspace
navigation and deck swipe/tap/drag behavior. Check actual iOS/Android keyboards,
system insets and hardware feedback before claiming real-device coverage.

`server/react-flow-touch.mjs` adds `touchable(() => true)` to the pinned XYFlow
camera initializer during Vite dependency optimization and production builds.
d3 otherwise checks touch only when the camera mounts, so enabling Edge/Chrome's
F12 device toolbar after loading a desktop page leaves it without touch handlers.
The transform keeps the native camera implementation and live editors mounted;
an upstream initializer change fails explicitly for review. The browser regression
starts without touch, enables emulation without reloading, then pans and pinches.
