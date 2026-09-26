import { expect, it } from 'vitest';
import type { LibrarySnapshot } from '../state/cardLibrary';
import { packInventory, type InstalledPack } from './installedPacks';

function snapshot(): LibrarySnapshot {
  return { schema_version: 1, revision: 1, migration_pending: false, plugins: {},
    packs: {
      core: { definition: { id: 'core', plugin_id: 'core', name: 'Core', description: '', cards: [], source: 'bundled' }, owned: true, opened: false, opened_at: null },
      local: { definition: { id: 'local', plugin_id: 'local.tools', name: 'Tools', description: '', cards: [], source: 'installed' }, owned: true, opened: true, opened_at: null },
    }, card_definitions: {}, collection: {}, decks: [], active_deck_id: '', available_card_ids: [], available_pack_ids: [] };
}
const version: InstalledPack = { id: 'local.tools', name: 'Tools', version: '1.0.0', selected: false, loaded: false, environment: null };

it('hides uninstalled packs both before and after restart, including saved collection records', () => {
  for (const versions of [[{ ...version, loaded: true }], [version], []]) {
    const inventory = packInventory(snapshot(), { restart_required: versions.some(item => item.loaded), versions });
    expect(inventory.map(item => item.pack.definition.id)).toEqual(['core']);
  }
});

it('does not synthesize an inventory pack for retained files from an uninstalled pack', () => {
  const state = snapshot();
  delete state.packs.local;
  expect(packInventory(state, { restart_required: false, versions: [version] }).map(item => item.pack.definition.id)).toEqual(['core']);
  expect(packInventory(state, { restart_required: true, versions: [{ ...version, selected: true }] })
    .map(item => item.pack.definition.id)).toEqual(['core', 'installed:local.tools']);
});

it('keeps selected packs without duplicating older retained versions', () => {
  const inventory = packInventory(snapshot(), { restart_required: true,
    versions: [version, { ...version, version: '2.0.0', selected: true }] });
  expect(inventory.map(item => item.pack.definition.id)).toEqual(['core', 'local']);
  expect(inventory[1].versions).toHaveLength(2);
});
