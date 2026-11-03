import { afterEach, beforeEach, expect, it } from 'vitest';
import { useInteractionEnvironment } from '../interaction/environment';
import { effectiveCanvasSurfaceLevel, useNodeSurfaceStore } from './nodeSurfaces';
import { TEST_CATALOG } from './catalog.fixture';
import { buildCardDraft } from './helpers';

beforeEach(() => {
  useInteractionEnvironment.setState({ compact: true });
  useNodeSurfaceStore.setState({ surfaceLevels: {}, baseLevels: {}, surfaceSizes: {}, mobileSurface: undefined, dragging: false, connectingNodeId: undefined });
});
afterEach(() => { useInteractionEnvironment.setState({ compact: false }); useNodeSurfaceStore.setState({ mobileSurface: undefined }); });

it('opens compact details without changing canvas levels, size or captured presentation', () => {
  const card = { id: 'text', ...buildCardDraft('text', { x: 240, y: 180 }) };
  const store = useNodeSurfaceStore.getState();
  store.syncCards([card], TEST_CATALOG);
  store.resizeSurface(card.id, 'inspector', { width: 800, height: 600 });
  const before = useNodeSurfaceStore.getState();
  const presentation = store.capturePresentation([card], TEST_CATALOG);
  store.openPrimary(card.id);
  expect(useNodeSurfaceStore.getState().mobileSurface).toEqual({ nodeId: card.id, level: 'inspector' });
  expect(useNodeSurfaceStore.getState().surfaceLevels).toBe(before.surfaceLevels);
  expect(useNodeSurfaceStore.getState().surfaceSizes).toBe(before.surfaceSizes);
  expect(store.capturePresentation([card], TEST_CATALOG)).toEqual(presentation);
  const persisted = useNodeSurfaceStore.persist.getOptions().partialize!(useNodeSurfaceStore.getState());
  expect(persisted).not.toHaveProperty('mobileSurface');
  store.closeMobileSurface();
  expect(useNodeSurfaceStore.getState().mobileSurface).toBeUndefined();
  expect(useNodeSurfaceStore.getState().surfaceLevels).toBe(before.surfaceLevels);
});

it('keeps desktop window preferences while presenting their compact canvas form', () => {
  const card = { id: 'agent', ...buildCardDraft('agent', { x: 0, y: 0 }) };
  expect(effectiveCanvasSurfaceLevel(card, 'workspace', true, TEST_CATALOG, 'node')).toBe('node');
  expect(effectiveCanvasSurfaceLevel(card, 'workspace', false, TEST_CATALOG, 'node')).toBe('workspace');
  expect(effectiveCanvasSurfaceLevel(card, 'inspector', true, TEST_CATALOG, 'preview')).toBe('preview');
});

it('preserves supported surfaces and blocks opening during canvas gestures', () => {
  const card = { id: 'text', ...buildCardDraft('text', { x: 0, y: 0 }) };
  const store = useNodeSurfaceStore.getState();
  store.syncCards([card], TEST_CATALOG);
  store.openWorkspace(card.id);
  expect(useNodeSurfaceStore.getState().mobileSurface?.level).toBe('inspector');
  store.closeExpanded();
  store.beginConnection(card.id); store.openPrimary(card.id);
  expect(useNodeSurfaceStore.getState().mobileSurface).toBeUndefined();
  store.endConnection(); store.setDragging(true); store.openPrimary(card.id);
  expect(useNodeSurfaceStore.getState().mobileSurface).toBeUndefined();
});

it('closes the mobile owner when a deleted card disappears from synchronization', () => {
  const card = { id: 'agent', ...buildCardDraft('agent', { x: 0, y: 0 }) };
  const store = useNodeSurfaceStore.getState();
  store.syncCards([card], TEST_CATALOG); store.openWorkspace(card.id);
  store.syncCards([], TEST_CATALOG);
  expect(useNodeSurfaceStore.getState().mobileSurface).toBeUndefined();
});
