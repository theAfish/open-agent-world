import { t } from '../i18n';

export interface HelpCheck {
  id: string; status: 'ok' | 'warning' | 'info'; code: string;
  node_id?: string | null; name?: string | null; focus_id?: string | null;
  x?: number | null; y?: number | null;
}
export interface HelpDiagnostics { checked_at: string; card_count: number; checks: HelpCheck[] }

export const DOCS_URL = 'https://theafish.github.io/open-agent-world/';
export const RELEASES_URL = 'https://github.com/theAfish/open-agent-world/releases';

// Only host-owned message codes are translated. Card names remain user content.
export function checkMessage(code: string): string {
  switch (code) {
    case 'backend': return t('The backend responded. Canvas data can be read.');
    case 'connections': return t('All saved connections have both endpoints.');
    case 'broken_connections': return t('Some connections point to missing cards. Refresh the canvas, then reconnect the affected cards.');
    case 'card_configured': return t('The card type is loaded and no saved error was found.');
    case 'card_error': return t('This card reports an error. Open it to review the latest failure and its settings, then retry there.');
    case 'plugin_unavailable': return t('This card needs an unavailable plugin. Open the Library to enable or reinstall its pack, then restart OAW.');
    case 'plugin_environment': return t('Pack dependencies could not be prepared. Check Sandbox availability and follow the pack troubleshooting guide.');
    case 'plugin_preparing': return t('Pack dependencies are still being prepared. Wait a moment, then check again.');
    case 'plugin_unverified': return t('The plugin is loaded. Its tools and external services have not been tested; open the card to check its own status.');
    case 'agent_runtime': return t('The Agent runtime is unavailable. Check its provider in the card settings and follow the runtime setup guide.');
    case 'external_agent': return t('This Agent uses its own runtime. Check login, model and service status in its card; OAW model settings do not apply.');
    case 'legacy_model': return t('This Agent uses a legacy model name. Check its provider credentials, or select a saved model in Settings.');
    case 'model_configuration': return t('The selected model or its credentials are missing, disabled or unreadable. Check Settings → Models and the Agent or Legion model selection.');
    case 'model_configured': return t('The selected model and credentials are configured. No paid request was sent; provider connectivity is untested.');
    case 'sandbox_unavailable': return t('This Sandbox runtime is unavailable. Open the card to see the reason, then follow the Sandbox setup guide.');
    case 'sandbox_network': return t('Sandbox networking is enabled but unavailable. Open its environment settings and repair the networking prerequisites.');
    case 'sandbox_stopped': return t('The Sandbox is stopped and its runtime is available. Start it from the card when needed.');
    case 'sandbox_available': return t('The Sandbox runtime is available. Commands and network access have not been tested.');
    case 'conversation_unconnected': return t('No Agent is connected to this Conversation. Connect one and add it to the session when you want replies.');
    case 'socket_live': return t('The live event stream is connected.');
    case 'socket_offline': return t('Live updates are disconnected. OAW is reconnecting automatically. If this continues, check the backend and network, then reopen OAW.');
    default: return t('This check could not finish. Check again, or open the card to inspect its status.');
  }
}

export function repairGuide(code: string): string {
  if (code.startsWith('sandbox')) return `${DOCS_URL}sandbox-workspace/`;
  if (code.startsWith('model') || code === 'legacy_model') return `${DOCS_URL}user-guide/models/`;
  if (code.startsWith('plugin')) return `${DOCS_URL}user-guide/plugins/`;
  return `${DOCS_URL}user-guide/troubleshooting/`;
}
