import { worldApi } from '../api/client';
import { mergeCards, useWorldStore } from '../state/worldStore';
import { containerSizes, isContainer } from '../state/containers';
import { surfaceSizeFor, useNodeSurfaceStore } from '../state/nodeSurfaces';
import { nodePositionFromSurfacePosition, positionSurfaceAtNodeCenter } from '../canvas/nodeDisplacement';
import { vacantPosition } from './placement';

/** Arrange only the newly deployed roots; container moves carry their owned members. */
export async function arrangeWorkspace(ids: string[], preset: boolean) {
  const world = () => useWorldStore.getState();
  if (preset) {
    for (const card of world().cards.filter(card => ids.includes(card.id))) {
      if (card.type === 'legion' && card.config.mode === 'group'
        && !(card.config.workspace_layout as { root?: unknown } | undefined)?.root
        && !world().edges.some(edge => edge.source === card.id || edge.target === card.id)) {
        await world().dissolveContainer(card.id, { silentSuccess: true });
        if (world().cards.some(item => item.id === card.id)) throw new Error('Could not dissolve the workspace group.');
      }
    }
  }
  const snapshot = await worldApi.getWorld();
  useWorldStore.setState(state => ({ cards: mergeCards(state.cards, snapshot.nodes, state.cardTombstones) }));
  const cards = snapshot.nodes;
  const byId = new Map(cards.map(card => [card.id, card]));
  const roots = ids.flatMap(id => { const card = byId.get(id); return card && !card.parent_id && !card.equipment ? [card] : []; });
  const surfaces = useNodeSurfaceStore.getState();
  surfaces.syncCards(cards, world().catalog);
  const { surfaceLevels, surfaceSizes } = useNodeSurfaceStore.getState();
  const sizes = containerSizes(cards, world().catalog, new Map(Object.entries(surfaceLevels)), surfaceSizes);
  const rect = (card: typeof cards[number]) => {
    const level = surfaceLevels[card.id] ?? 'preview';
    return { ...(isContainer(card, world().catalog) ? card.position : positionSurfaceAtNodeCenter(card.position, level)),
      ...(sizes.get(card.id) ?? surfaceSizeFor(card.id, level, surfaceSizes)) };
  };
  const left = Math.min(...roots.map(card => rect(card).x));
  const top = Math.min(...roots.map(card => rect(card).y));
  const columns = Math.min(3, Math.ceil(Math.sqrt(roots.length)));
  let x = 0, y = 0, rowHeight = 0;
  const cells = roots.map((card, index) => {
    if (index && index % columns === 0) { x = 0; y += rowHeight + 100; rowHeight = 0; }
    const box = rect(card);
    // Saved blueprints retain their authored arrangement; only bundled presets are reflowed.
    const cell = { card, x: preset ? x : box.x - left, y: preset ? y : box.y - top, width: box.width, height: box.height };
    x += box.width + 100; rowHeight = Math.max(rowHeight, box.height);
    return cell;
  });
  if (!cells.length) return undefined;
  const width = Math.max(...cells.map(cell => cell.x + cell.width));
  const height = Math.max(...cells.map(cell => cell.y + cell.height));
  const viewport = world().viewport;
  const origin = vacantPosition({ x: (viewport.width / 2 - viewport.x) / viewport.zoom - width / 2,
    y: (viewport.height / 2 - viewport.y) / viewport.zoom - height / 2, width, height },
    cards.filter(card => !ids.includes(card.id) && !card.parent_id && !card.equipment).map(rect), 180);
  await world().updateCardPositions(cells.map(({ card, x, y }) => ({ id: card.id,
    position: isContainer(card, world().catalog) ? { x: origin.x + x, y: origin.y + y }
      : nodePositionFromSurfacePosition({ x: origin.x + x, y: origin.y + y }, surfaceLevels[card.id] ?? 'preview') })));
  return { ...origin, width, height };
}
