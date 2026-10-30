import { expect, test } from '@playwright/test';
import type { LibrarySnapshot } from '../src/state/cardLibrary';

test('short card details fit their content without empty space beside or below it', async ({ page, request }, info) => {
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }) },
  } });
  const initial = await (await request.get('/api/card-library')).json() as LibrarySnapshot;
  const pack = Object.values(initial.packs).find(item => item.definition.cards.includes('oaw.barracks'))!;
  expect((await request.post('/api/card-library/actions', { data: {
    action: 'open_pack', id: pack.definition.id, expected_revision: initial.revision,
  } })).ok()).toBe(true);
  await page.setViewportSize({ width: 1280, height: 1100 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  await library.getByRole('button', { name: /^Cards/ }).click();
  await library.locator('[data-library-card="oaw.barracks"]').click();
  const sidebar = library.locator('.library-card-sidebar');
  for (const viewport of [{ width: 1280, height: 1100 }, { width: 720, height: 1100 }, { width: 390, height: 1600 }]) {
    await page.setViewportSize(viewport);
    for (const theme of ['light', 'dark']) {
      await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
      await page.screenshot({ path: info.outputPath(`short-details-${viewport.width}-${theme}.png`), animations: 'disabled' });
      expect(await sidebar.evaluate(element => {
        const panel = element.getBoundingClientRect();
        const content = element.querySelector('.library-card-detail')!.getBoundingClientRect();
        const viewport = element.querySelector('[data-overlayscrollbars-viewport]')!;
        return {
          rightGap: panel.right - content.right,
          bottomGap: panel.bottom - content.bottom,
          scrollable: viewport.scrollHeight > viewport.clientHeight + 1,
        };
      })).toEqual({ rightGap: 1, bottomGap: 1, scrollable: false });
      await expect(library.getByRole('button', { name: 'Add inspected card to deck' })).toBeInViewport();
    }
  }
});

test('collection and details keep independent scrolling and themed overlay controls across screen sizes', async ({ page, request }, info) => {
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }) },
  } });
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
  const collection = library.locator('.library-card-collection [data-overlayscrollbars-viewport]');
  const sidebar = library.locator('.library-card-sidebar');
  const detailViewport = sidebar.locator('[data-overlayscrollbars-viewport]');
  const scrollbar = sidebar.locator('.os-theme-oaw.os-scrollbar-vertical');
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
    await detailViewport.evaluate(element => { element.scrollTop = 0; });
    await collection.evaluate(element => { element.scrollTop = 0; });
    expect(await collection.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
    expect(await detailViewport.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
    await expect.poll(async () => {
      const panel = (await sidebar.boundingBox())!, card = (await preview.boundingBox())!;
      return card.width <= 185 && card.x >= panel.x && card.x + card.width <= panel.x + panel.width
        && card.y >= panel.y && card.y + card.height <= panel.y + panel.height;
    }).toBe(true);
    // The scroll viewport must clip the panel behind the sticky actions too.
    for (const theme of ['light', 'dark']) {
      await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
      await sidebar.hover();
      await expect(scrollbar).toBeVisible();
      expect(await sidebar.evaluate(element => {
        const { left, right, top, bottom } = element.getBoundingClientRect();
        return [[left + 2, top + 2], [right - 2, top + 2], [left + 2, bottom - 2], [right - 2, bottom - 2]]
          .map(([x, y]) => Boolean(document.elementFromPoint(x, y)?.closest('.library-card-sidebar')));
      })).toEqual([false, false, false, false]);
      await expect(library.getByRole('button', { name: 'Add inspected card to deck' })).toBeInViewport();
      await page.screenshot({ path: `test-results/library-preview-rounded-${viewport.width}-${theme}.png` });
    }
    await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
    await page.screenshot({ path: `test-results/library-preview-layout-${viewport.width}.png` });
    const collectionScroll = await collection.evaluate(element => element.scrollTop);
    await sidebar.hover();
    await page.mouse.wheel(0, 280);
    await expect.poll(() => detailViewport.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    expect(await collection.evaluate(element => element.scrollTop)).toBe(collectionScroll);
    expect(await library.locator('.library-body').evaluate(element => element.scrollTop)).toBe(0);
    const sidebarScroll = await detailViewport.evaluate(element => element.scrollTop);
    await collection.hover();
    await page.mouse.wheel(0, 280);
    await expect.poll(() => collection.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    expect(await detailViewport.evaluate(element => element.scrollTop)).toBe(sidebarScroll);
  }

  await page.setViewportSize({ width: 1280, height: 700 });
  await detailViewport.evaluate(element => { element.scrollTop = 0; });
  await sidebar.hover();
  await expect(scrollbar).toBeVisible();
  const handle = scrollbar.locator('.os-scrollbar-handle');
  const box = (await handle.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 + 40, { steps: 8 });
  await page.mouse.up();
  await expect.poll(() => detailViewport.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
  const draggedTo = await detailViewport.evaluate(element => element.scrollTop);
  await detailViewport.focus();
  await page.keyboard.press('PageDown');
  await expect.poll(() => detailViewport.evaluate(element => element.scrollTop)).toBeGreaterThan(draggedTo);
  await page.mouse.move(1, 1);
  await expect(scrollbar).toBeHidden();
  await page.screenshot({ path: info.outputPath('overlay-scrollbars-idle.png') });
  const beforeTrackClick = await detailViewport.evaluate(element => element.scrollTop);
  await sidebar.hover();
  const track = scrollbar.locator('.os-scrollbar-track');
  const trackBox = (await track.boundingBox())!;
  await track.click({ position: { x: trackBox.width / 2, y: trackBox.height - 10 } });
  await expect.poll(() => detailViewport.evaluate(element => element.scrollTop)).toBeGreaterThan(beforeTrackClick);

  await library.getByLabel('Search cards', { exact: true }).fill('Agent Barracks');
  await library.locator('[data-library-card="oaw.barracks"]').click();
  await expect.poll(() => detailViewport.evaluate(element => element.scrollTop)).toBe(0);
  await library.getByRole('button', { name: /^Packs/ }).click();
  await library.getByRole('button', { name: /^Cards/ }).click();
  await expect(library.locator('.os-theme-oaw.os-scrollbar-vertical')).toHaveCount(2);
  await expect(library.getByRole('complementary', { name: 'Card details' }).getByRole('heading', { name: 'Agent Barracks' })).toBeVisible();
});
