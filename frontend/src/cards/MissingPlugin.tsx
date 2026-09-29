import { t } from '../i18n';
import type { PluginCatalog, WorldCard } from '../types/world';
import './missingPlugin.css';

export function isMissingCard(card: WorldCard, catalog: PluginCatalog): boolean {
  return !card.ephemeral && (Boolean(card.missing_plugin)
    || (catalog.node_types.length > 0 && !catalog.node_types.some(item => item.id === card.type)));
}

export function MissingPlugin({ card, compact = false }: { card: WorldCard; compact?: boolean }) {
  return <div className="missing-plugin-message" data-missing-plugin={card.missing_plugin?.plugin_id ?? card.type}>
    <strong>MISSING</strong>
    {!compact && <span>{t('Card implementation unavailable')}</span>}
    <code>{card.type}</code>
    {card.missing_plugin && <code>{card.missing_plugin.plugin_id}</code>}
    {!compact && <p>{t('Your card data is preserved. Restore its pack to use it again, or move, rename, or delete this card.')}</p>}
  </div>;
}
