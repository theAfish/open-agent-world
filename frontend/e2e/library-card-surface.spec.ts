import { expect, test, type Locator } from '@playwright/test';
import type { LibrarySnapshot } from '../src/state/cardLibrary';

test('custom covers and authored faces use the same print in the collection, inspector and deck', async ({ page, request }, info) => {
  test.setTimeout(90_000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw.locale': 'en', 'oaw-theme': 'light',
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
    },
  } })).ok()).toBe(true);
  const read = async (): Promise<LibrarySnapshot> => (await request.get('/api/card-library')).json();
  let state = await read();
  const originalDeck = state.active_deck_id;
  const core = Object.values(state.packs).find(pack => pack.definition.cards.includes('text'))!;
  const xrd = Object.values(state.packs).find(pack => pack.definition.cards.includes('xrd.match'))!;
  for (const pack of [core, xrd]) {
    if (pack.opened) continue;
    const opened = await request.post('/api/card-library/actions', { data: {
      action: 'open_pack', id: pack.definition.id, expected_revision: state.revision,
    } });
    expect(opened.ok()).toBe(true);
    state = await opened.json();
  }
  const formation = Object.entries(state.preset_pack_ids!).find(([, ids]) => ids.includes(xrd.definition.id))![0];
  const created = await request.post('/api/card-library/actions', { data: {
    action: 'create_deck', name: 'Shared card surface', expected_revision: state.revision,
    entries: [{ kind: 'node', id: 'text' }, { kind: 'legion', id: formation }],
  } });
  expect(created.ok()).toBe(true);
  state = await created.json();
  const studyDeck = state.active_deck_id;
  try {
    // Exercise a developer-authored catalog face without adding a plugin fixture.
    state.card_definitions.text = { ...state.card_definitions.text, label: 'Custom analysis',
      description: 'A developer-authored card with an image, a title and a description.',
      card_face: { variant: 'image', tone: 'sky', image_url: xrd.definition.artwork_url },
    };
    await page.route('**/api/card-library', route => route.fulfill({ json: state }));
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto('/');
    await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
    const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
    await library.getByRole('button', { name: /^Cards/ }).click();
    const preview = library.locator('.library-card-preview');
    const deck = page.getByRole('complementary', { name: 'Active card deck' });
    const search = library.getByLabel('Search cards', { exact: true });
    const geometry = (stock: Locator) => stock.locator('.card-face').evaluate(element => {
      const face = element as HTMLElement;
      return ['.card-face-edition', '.card-face-art', '.card-face-copy', '.card-face-copy strong', '.card-face-copy small', '.card-face-detail']
        .flatMap(selector => {
          const part = face.querySelector<HTMLElement>(selector)!;
          return [part.offsetWidth / face.clientWidth, part.offsetHeight / face.clientHeight,
            parseFloat(getComputedStyle(part).fontSize) / face.clientWidth];
        });
    });
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 720, height: 650 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      await page.evaluate(dark => { document.documentElement.dataset.theme = dark ? 'dark' : 'light'; }, viewport.width === 720);
      await library.getByLabel('Source pack', { exact: true }).selectOption(`pack:${xrd.definition.id}`);
      const thumbnail = library.locator(`[data-library-card="${formation}"]`);
      await thumbnail.click();
      const hand = deck.locator(`[data-palette-card="${formation}"]`);
      for (const stock of [thumbnail.locator('.card-stock'), preview, hand.locator('.card-stock')]) {
        const cover = stock.locator(':scope > .card-face-cover');
        await expect(cover).toHaveAttribute('src', xrd.definition.artwork_url!);
        await expect.poll(() => cover.evaluate(element => (element as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
        await expect(stock.locator('.card-face, .card-face-copy')).toHaveCount(0);
        expect(await stock.evaluate(element => {
          const card = element as HTMLElement, image = card.querySelector('img')!;
          return image.offsetWidth >= card.clientWidth && image.offsetHeight >= card.clientHeight;
        })).toBe(true);
      }
      await page.mouse.move(1, 1);
      await deck.hover();
      await page.screenshot({ path: info.outputPath(`shared-cover-${viewport.width}.png`), animations: 'disabled' });
      await library.getByLabel('Source pack', { exact: true }).selectOption('');
      await search.fill('Custom analysis');
      const custom = library.locator('[data-library-card="text"]');
      await custom.click();
      const customHand = deck.locator('[data-palette-card="text"]');
      const reference = await geometry(preview);
      for (const stock of [custom.locator('.card-stock'), customHand.locator('.card-stock')]) {
        await expect(stock.locator('.card-face')).toHaveAttribute('data-variant', 'image');
        await expect(stock.locator('.card-face')).toHaveAttribute('data-tone', 'sky');
        await expect(stock.locator('.card-face')).toHaveText((await preview.locator('.card-face').textContent())!);
        await expect(stock.locator('.card-face-copy small')).toBeVisible();
        const actual = await geometry(stock);
        actual.forEach((value, index) => expect(Math.abs(value - reference[index]),
          `${viewport.width}px print geometry ${index}: ${JSON.stringify({ actual, reference })}`).toBeLessThan(.025));
      }
      expect(await library.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
      await page.mouse.move(1, 1);
      await deck.hover();
      await page.screenshot({ path: info.outputPath(`shared-authored-${viewport.width}.png`), animations: 'disabled' });
      await search.fill('');
    }
    expect(errors).toEqual([]);
  } finally {
    state = await read();
    const restored = await request.post('/api/card-library/actions', { data: {
      action: 'activate_deck', id: originalDeck, expected_revision: state.revision,
    } });
    expect(restored.ok()).toBe(true);
    state = await restored.json();
    expect((await request.post('/api/card-library/actions', { data: {
      action: 'delete_deck', id: studyDeck, expected_revision: state.revision,
    } })).ok()).toBe(true);
  }
});
