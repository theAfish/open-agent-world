import type { PluginCatalog } from '../types/world';
import type { TutorialEntry, TutorialText } from './types';

export const tutorialOwner = (kind: 'card' | 'pack', id: string) => JSON.stringify([kind, id]);
export const tutorialKey = (owner: string, id: string) => JSON.stringify([owner, id]);
export const tutorialText = (text: TutorialText, locale: string): string =>
  typeof text === 'string' ? text : text[locale] ?? text.en;

export function tutorialEntries(catalog: PluginCatalog): TutorialEntry[] {
  return [
    ...catalog.node_types.map(card => ({ kind: 'card' as const, id: card.id, name: card.label, tutorials: card.tutorials })),
    ...catalog.packs.map(pack => ({ kind: 'pack' as const, id: pack.id, name: pack.name, tutorials: pack.tutorials })),
  ].flatMap(item => {
    const owner = tutorialOwner(item.kind, item.id);
    return (item.tutorials ?? []).map(definition => ({ key: tutorialKey(owner, definition.id), owner, ownerName: item.name, definition }));
  });
}
