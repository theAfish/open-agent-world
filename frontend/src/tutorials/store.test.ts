import { describe, expect, it } from 'vitest';
import { tutorialKey, tutorialOwner, tutorialText } from './catalog';
import { createTutorialStore, currentStep } from './store';
import type { TutorialDefinition, TutorialEntry } from './types';

const owner = tutorialOwner('card', 'example.card');
const entry = (id = 'intro', changes: Partial<TutorialDefinition> = {}, source = owner): TutorialEntry => ({
  key: tutorialKey(source, id), owner: source, ownerName: 'Example',
  definition: { id, title: 'Introduction', summary: 'Learn this card', steps: [
    { id: 'open', title: 'Open it', body: 'Open the card' }, { id: 'edit', title: 'Edit it', body: 'Edit the content' },
  ], ...changes },
});
function memory() {
  const items = new Map<string, string>();
  return { getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => { items.set(key, value); }, removeItem: (key: string) => { items.delete(key); } };
}

describe('progressive tutorial engine', () => {
  it('offers only encountered, automatic content, one item at a time', () => {
    const store = createTutorialStore(memory()), intro = entry(), manual = entry('reference', { trigger: 'manual' });
    store.getState().sync([intro, manual, entry('article', { steps: [], document: 'Reference' })]);
    expect(store.getState().current).toBeUndefined();
    store.getState().encounter([owner]); store.getState().encounter([owner]);
    expect(store.getState().current).toBe(intro.key);
    store.getState().dismiss();
    expect(store.getState().current).toBeUndefined();
  });

  it('persists dismissal and the last step across reloads, with manual resume', () => {
    const storage = memory(), store = createTutorialStore(storage), intro = entry();
    store.getState().sync([intro]); store.getState().encounter([owner]);
    store.getState().open(intro.key); store.getState().move(1); store.getState().dismiss();
    const next = createTutorialStore(storage);
    next.getState().sync([intro]); next.getState().encounter([owner]);
    expect(next.getState().current).toBeUndefined();
    next.getState().open(intro.key);
    expect(currentStep(intro, next.getState().progress[intro.key])).toBe(1);
    const saved = JSON.parse(storage.getItem('oaw-progressive-tutorials-v1')!).state;
    expect(Object.keys(saved).sort()).toEqual(['enabled', 'progress']);
  });

  it('keeps manual help available when automatic hints are disabled', () => {
    const store = createTutorialStore(memory()), intro = entry();
    store.getState().sync([intro]); store.getState().setEnabled(false); store.getState().encounter([owner]);
    expect(store.getState().current).toBeUndefined();
    store.getState().open(intro.key);
    expect(store.getState().view).toBe('steps');
    store.getState().dismiss();
    expect(store.getState().current).toBeUndefined();
  });

  it('unlocks prerequisites after completion and isolates the same ID by owner', () => {
    const store = createTutorialStore(memory()), intro = entry(), advanced = entry('advanced', { after: ['intro'] });
    const pack = entry('intro', {}, tutorialOwner('pack', 'example.card'));
    store.getState().sync([advanced, intro, pack]); store.getState().encounter([owner, pack.owner]);
    expect(store.getState().current).toBe(intro.key);
    store.getState().complete(); expect(store.getState().current).toBe(advanced.key);
    store.getState().dismiss(); expect(store.getState().current).toBe(pack.key);
  });

  it('offers revised completed content, preserves dismissals, and clears unavailable entries', () => {
    const store = createTutorialStore(memory()), intro = entry();
    store.getState().sync([intro]); store.getState().encounter([owner]); store.getState().complete();
    store.getState().sync([entry('intro', { revision: 2 })]); expect(store.getState().current).toBe(intro.key);
    store.getState().dismiss(); store.getState().sync([entry('intro', { revision: 3 })]);
    expect(store.getState().current).toBeUndefined();
    store.getState().open(intro.key); store.getState().sync([]);
    expect(store.getState().current).toBeUndefined();
  });

  it('falls back to English and restarts changed steps safely', () => {
    expect(tutorialText({ en: 'Hello', 'zh-CN': '你好' }, 'fr')).toBe('Hello');
    expect(currentStep(entry(), { revision: 1, status: 'reading', stepId: 'removed' })).toBe(0);
    expect(currentStep(entry('intro', { revision: 2 }), { revision: 1, status: 'reading', stepId: 'edit' })).toBe(0);
  });
});


it('replaying a finished chapter does not revoke completion or lock its dependents', () => {
  const store = createTutorialStore(memory()), intro = entry();
  store.getState().sync([intro, entry('advanced', { after: ['intro'] })]);
  store.getState().encounter([owner]); store.getState().complete();
  store.getState().open(intro.key); store.getState().move(1); store.getState().dismiss();
  expect(store.getState().progress[intro.key].status).toBe('completed');
  expect(store.getState().current).toBe(entry('advanced').key);
});
