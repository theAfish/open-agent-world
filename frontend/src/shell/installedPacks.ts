import type { LibrarySnapshot } from '../state/cardLibrary';
import type { PackInstallations } from '../types/packs';
import type { PackNotice } from './pack3d/types';

export type InstalledPack = PackInstallations['versions'][number];

export function packNotice(status?: string): PackNotice | undefined {
  if (!status || status === 'Installed' || status === 'Loaded') return undefined;
  const kind = ['Pack uninstalled', 'Pack unavailable', 'Environment failed', 'Uninstall pending restart'].includes(status) ? 'warning'
    : status === 'Update available' ? 'update' : status === 'Restart required' ? 'restart' : 'info';
  return { kind, label: status };
}

/** Keep pending restarts and intentional disabling separate from broken packs. */
export function packIssue(pack: LibrarySnapshot['packs'][string], snapshot: LibrarySnapshot, versions: InstalledPack[] = []) {
  const loaded = versions.find(item => item.loaded);
  const selected = versions.find(item => item.selected);
  if ((loaded ?? selected)?.environment?.state === 'environment_failed') {
    return { kind: 'error', label: 'Environment failed' } as const;
  }
  if (selected && !selected.loaded) return undefined;
  const plugin = snapshot.plugins[pack.definition.plugin_id];
  if (!plugin?.installed) return { kind: 'missing', label: 'Pack uninstalled' } as const;
  if (plugin.enabled && !snapshot.available_pack_ids.includes(pack.definition.id)) {
    return { kind: 'missing', label: 'Pack unavailable' } as const;
  }
  return undefined;
}

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
    const owner = item.owner ?? item.id;
    byPlugin.set(owner, [...(byPlugin.get(owner) ?? []), item]);
  }
  const packs = Object.values(snapshot.packs).filter(pack => {
    if (pack.dismissed) return false;
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
      description: item.creator?.description ?? '', accent_color: item.creator?.accent_color || undefined, packaging: item.creator?.packaging, cards: [] },
      owned: true, opened: false, opened_at: null });
  }
  for (const item of installations?.inventory ?? []) {
    if (item.source === 'installed' || packs.some(pack => pack.definition.plugin_id === item.owner)) continue;
    packs.push({ definition: { id: `source:${item.owner}`, plugin_id: item.owner, name: item.name,
      description: '', cards: [], source: 'bundled' }, owned: true, opened: false, opened_at: null });
  }
  return packs.map(pack => ({ pack, versions: byPlugin.get(pack.definition.plugin_id) ?? [],
    inventory: installations?.inventory?.find(item => item.owner === pack.definition.plugin_id),
    failures: installations?.failures?.filter(failure => failure.owner ? failure.owner === pack.definition.plugin_id
      : failure.id === pack.definition.plugin_id
      || installations.inventory?.some(item => item.owner === pack.definition.plugin_id && item.id === failure.id)
      || byPlugin.get(pack.definition.plugin_id)?.some(item => item.id === failure.id)) ?? [],
    registered: Boolean(snapshot.packs[pack.definition.id]) }));
}
