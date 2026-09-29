import { expect, it } from 'vitest';
import type { LibrarySnapshot } from '../state/cardLibrary';
import { packInventory, packIssue, type InstalledPack } from './installedPacks';

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

it('does not mark a new installation waiting for restart as missing', () => {
  const state = snapshot();
  expect(packIssue(state.packs.local, state, [{ ...version, selected: true }])).toBeUndefined();
  expect(packIssue(state.packs.local, state)).toEqual({ kind: 'missing', label: 'Pack uninstalled' });
});

it('distinguishes disabled and preparing packs from unavailable and failed packs', () => {
  const state = snapshot();
  state.plugins['local.tools'] = { descriptor: { id: 'local.tools', name: 'Tools', description: '', version: '1.0.0', plugin_api_version: '1.0' }, installed: true, enabled: false };
  expect(packIssue(state.packs.local, state)).toBeUndefined();
  state.plugins['local.tools'].enabled = true;
  expect(packIssue(state.packs.local, state)?.kind).toBe('missing');
  state.available_pack_ids = ['local'];
  const loaded = { ...version, selected: true, loaded: true, environment: { state: 'environment_preparing', error: null } };
  expect(packIssue(state.packs.local, state, [loaded])).toBeUndefined();
  loaded.environment.state = 'environment_failed';
  expect(packIssue(state.packs.local, state, [loaded])).toEqual({ kind: 'error', label: 'Environment failed' });
  loaded.environment.state = 'environment_ready';
  expect(packIssue(state.packs.local, state, [loaded])).toBeUndefined();
});
