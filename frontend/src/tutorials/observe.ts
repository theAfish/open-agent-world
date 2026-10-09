import { observeInteractions } from '../state/interactions';
import { useWorldStore } from '../state/worldStore';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { useCardLibrary } from '../state/cardLibrary';
import { useTutorialStore } from '../onboarding/controller';
import { tutorialEntries, tutorialOwner } from './catalog';
import { useProgressiveTutorials } from './store';

/** Observe deliberate encounters, never viewport loads or background graph updates. */
export function observeTutorialEncounters() {
  const store = useProgressiveTutorials;
  const allowed = () => useTutorialStore.getState().view === 'hidden' && !useTutorialStore.getState().quickStart;
  const encounterCard = (type: string) => {
    if (!allowed()) return;
    const catalog = useWorldStore.getState().catalog;
    store.getState().encounter([tutorialOwner('card', type), ...catalog.packs
      .filter(pack => pack.cards.includes(type)).map(pack => tutorialOwner('pack', pack.id))]);
  };
  const encounterIds = (ids: string[]) => {
    const cards = useWorldStore.getState().cards;
    for (const id of ids) { const card = cards.find(item => item.id === id); if (card) encounterCard(card.type); }
  };
  store.getState().sync(tutorialEntries(useWorldStore.getState().catalog));
  const unsubscribes = [
    observeInteractions(event => { if (event.type === 'card-inspected') encounterCard(event.cardType); }),
    useWorldStore.subscribe((state, previous) => {
      if (state.catalog !== previous.catalog) store.getState().sync(tutorialEntries(state.catalog));
      if (state.selectedCardIds !== previous.selectedCardIds) encounterIds(state.selectedCardIds.filter(id => !previous.selectedCardIds.includes(id)));
      if (state.undoStack !== previous.undoStack) {
        const operation = state.undoStack.at(-1);
        if (operation?.kind === 'card-created' && operation.id !== previous.undoStack.at(-1)?.id)
          operation.cards.forEach(card => encounterCard(card.type));
      }
    }),
    useNodeSurfaceStore.subscribe((state, previous) => {
      if (state.surfaceLevels === previous.surfaceLevels) return;
      encounterIds(Object.keys(state.surfaceLevels).filter(id => previous.surfaceLevels[id] !== undefined && state.surfaceLevels[id] !== previous.surfaceLevels[id]
        && ['inspector', 'workspace'].includes(state.surfaceLevels[id])));
    }),
    useCardLibrary.subscribe((state, previous) => {
      if (!allowed()) return;
      if (state.inspectedEntry !== previous.inspectedEntry && state.inspectedEntry?.kind === 'node') encounterCard(state.inspectedEntry.id);
      if (state.snapshot !== previous.snapshot && previous.snapshot) {
        store.getState().encounter(Object.entries(state.snapshot?.packs ?? {})
          .filter(([id, pack]) => pack.opened && !previous.snapshot!.packs[id]?.opened)
          .map(([id]) => tutorialOwner('pack', id)));
      }
    }),
  ];
  return () => unsubscribes.forEach(unsubscribe => unsubscribe());
}
