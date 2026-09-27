import { expect, test } from '@playwright/test';
import type { LibrarySnapshot } from '../src/state/cardLibrary';

test('collection and card details scroll independently while the preview fits smaller screens', async ({ page, request }) => {
  const initial = await (await request.get('/api/card-library')).json() as LibrarySnapshot;
  const pack = Object.values(initial.packs).find(item => item.definition.cards.includes('text'))!;
  const opened = await request.post('/api/card-library/actions', { data: {
    action: 'open_pack', id: pack.definition.id, expected_revision: initial.revision,
  } });
  expect(opened.ok()).toBe(true);
  const libraryState = await opened.json() as LibrarySnapshot;
  // Guarantee several visible rows regardless of the real pack's current contents.
  const cardTemplate = libraryState.card_definitions.text;
  const entryTemplate = libraryState.collection.text;
  const fixturePack = libraryState.packs[pack.definition.id];
  for (let index = 1; index <= 24; index++) {
    const id = `layout-preview-${index}`;
    libraryState.card_definitions[id] = { ...cardTemplate, id,
      label: `Layout card ${String(index).padStart(2, '0')}`, user_creatable: true };
    libraryState.collection[id] = { ...entryTemplate, card_id: id, source_pack_ids: [pack.definition.id] };
    libraryState.available_card_ids.push(id);
    fixturePack.definition.cards.push(id);
  }
  // Third-party packs can provide long descriptions; they must not stretch the dialog.
  libraryState.card_definitions.text.description = Array(12).fill('A document card with editable content, attachments, and a reusable place in your collection.').join(' ');
  await page.route('**/api/card-library', route => route.fulfill({ json: libraryState }));
  await page.setViewportSize({ width: 1280, height: 700 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  await library.getByRole('button', { name: /^Cards/ }).click();
  await library.getByLabel('Search cards', { exact: true }).fill('Text file');
  await library.locator('[data-library-card="text"]').click();
  await library.getByLabel('Search cards', { exact: true }).fill('');
  await library.getByLabel('Source pack', { exact: true }).selectOption(`pack:${pack.definition.id}`);
  const collection = library.locator('.library-card-collection');
  const sidebar = library.locator('.library-card-sidebar');
  const preview = library.locator('.library-card-preview');
  await preview.hover({ position: { x: 25, y: 25 } });
  await expect(preview).toHaveAttribute('data-card-tilting', 'true');
  await page.mouse.move(1, 1);
  await expect(preview).not.toHaveAttribute('data-card-tilting');
  const thumbnail = library.locator('.library-card-stock').first();
  await thumbnail.hover({ position: { x: 20, y: 20 } });
  await expect(thumbnail).toHaveAttribute('data-card-tilting', 'true');
  await page.mouse.move(1, 1);
  await expect(thumbnail).not.toHaveAttribute('data-card-tilting');

  for (const viewport of [{ width: 1280, height: 700 }, { width: 720, height: 650 }, { width: 390, height: 800 }]) {
    await page.setViewportSize(viewport);
    await sidebar.evaluate(element => { element.scrollTop = 0; });
    await collection.evaluate(element => { element.scrollTop = 0; });
    expect(await collection.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
    expect(await sidebar.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
    await expect.poll(async () => {
      const panel = (await sidebar.boundingBox())!, card = (await preview.boundingBox())!;
      return card.width <= 185 && card.x >= panel.x && card.x + card.width <= panel.x + panel.width
        && card.y >= panel.y && card.y + card.height <= panel.y + panel.height;
    }).toBe(true);
    await page.screenshot({ path: `test-results/library-preview-layout-${viewport.width}.png` });
    const collectionScroll = await collection.evaluate(element => element.scrollTop);
    await sidebar.hover();
    await page.mouse.wheel(0, 280);
    await expect.poll(() => sidebar.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    expect(await collection.evaluate(element => element.scrollTop)).toBe(collectionScroll);
    expect(await library.locator('.library-body').evaluate(element => element.scrollTop)).toBe(0);
    const sidebarScroll = await sidebar.evaluate(element => element.scrollTop);
    await collection.hover();
    await page.mouse.wheel(0, 280);
    await expect.poll(() => collection.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    expect(await sidebar.evaluate(element => element.scrollTop)).toBe(sidebarScroll);
  }
});
