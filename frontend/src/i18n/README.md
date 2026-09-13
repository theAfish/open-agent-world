# Interface localization

`messages.json` maps UI message keys to `[English, Simplified Chinese]`. English
source text is the key for new messages; legacy Chinese keys are supported.

Components that render translations subscribe with `useLocale()` and call
`t('Place {name}', { name })`. Translate complete sentences so each locale can
reorder placeholders. The subscription updates existing components without
remounting forms. Use the selected locale for date/number formatting as well.

The language control and Settings switch between `en` and `zh-CN`. The choice is
stored in `oaw.locale`; first use follows the browser language. Storage failures
do not prevent switching during the current session. `App` updates the HTML lang.

Only translate interface copy and registered display metadata. Keep identifiers,
enum values, API payloads, user names, messages, documents, model output, and
technical diagnostics intact. Unknown keys fall back to the original string.
Plugins can import `t` and `useLocale` from `@oaw/plugin-api`.

Run `npm test` for live-switching, draft preservation, interpolation, tutorial
coverage and placeholder checks. `e2e/i18n-hud.spec.ts` verifies HUD layout and
screen-space terrain line widths with the real React Flow zoom controls.
