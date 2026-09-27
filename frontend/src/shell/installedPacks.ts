import type { LibrarySnapshot } from '../state/cardLibrary';
import type { PackInstallations } from '../types/packs';

export type InstalledPack = PackInstallations['versions'][number];

export function installedPackStatus(versions: InstalledPack[]) {
  const selected = versions.find(item => item.selected);
  const loaded = versions.find(item => item.loaded);
  if (selected && !selected.loaded) return 'Restart required';
  if (!selected && loaded) return 'Uninstall pending restart';
  if (!selected) return 'Retained version';
  if (loaded?.environment?.state === 'environment_failed') return 'Environment failed';
  if (loaded?.environment && loaded.environment.state !== 'environment_ready') return 'Environment preparing';
  return 'Installed';
}

/** Pending installations share the inventory without pretending their cards are loaded. */
export function packInventory(snapshot: LibrarySnapshot, installations?: PackInstallations) {
  const byPlugin = new Map<string, InstalledPack[]>();
  for (const item of installations?.versions ?? []) {
    byPlugin.set(item.id, [...(byPlugin.get(item.id) ?? []), item]);
  }
  const packs = Object.values(snapshot.packs).filter(pack => {
    const versions = byPlugin.get(pack.definition.plugin_id);
    // Loaded runtimes and saved collection records can outlive an uninstall.
    if (versions) return versions.some(item => item.selected);
    if (pack.definition.source === 'installed') {
      return !installations && Boolean(snapshot.plugins[pack.definition.plugin_id]?.installed);
    }
    return true;
  });
  const represented = new Set(packs.map(pack => pack.definition.plugin_id));
  for (const [id, versions] of byPlugin) {
    if (represented.has(id)) continue;
    const item = versions.find(row => row.selected);
    if (!item) continue;
    packs.push({ definition: { id: `installed:${id}`, plugin_id: id, name: item.name,
      description: item.creator?.description ?? '', accent_color: item.creator?.accent_color || undefined, cards: [] },
      owned: true, opened: false, opened_at: null });
  }
  return packs.map(pack => ({ pack, versions: byPlugin.get(pack.definition.plugin_id) ?? [],
    registered: Boolean(snapshot.packs[pack.definition.id]) }));
}
