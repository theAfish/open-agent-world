import { expect, test, type Locator } from '@playwright/test';
import type { LibrarySnapshot } from '../src/state/cardLibrary';

test('default collectible prints stay consistent through inspection, themes, deck placement and reload', async ({ page, request }, info) => {
  test.setTimeout(60_000);
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
  const core = Object.values(state.packs).find(pack => pack.definition.cards.includes('agent'))!;
  if (!core.opened) {
    const response = await request.post('/api/card-library/actions', { data: {
      action: 'open_pack', id: core.definition.id, expected_revision: state.revision,
    } });
    expect(response.ok()).toBe(true);
    state = await response.json();
  }
  // Use a separate deck so this visual acceptance test leaves other suites' hands intact.
  const originalDeck = state.active_deck_id;
  const created = await request.post('/api/card-library/actions', { data: {
    action: 'create_deck', name: 'Card face study', icon: 'layers', expected_revision: state.revision,
  } });
  expect(created.ok()).toBe(true);
  const studyDeck = (await created.json() as LibrarySnapshot).active_deck_id;
  let placedId: string | undefined;
  try {
    const definitions = state.card_definitions;
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto('/');
    const open = page.getByRole('button', { name: 'Open Pack and Card Library' });
    await open.click();
    const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
    await library.getByRole('button', { name: /^Cards/ }).click();
    await library.getByLabel('Source pack', { exact: true }).selectOption(`pack:${core.definition.id}`);
    const preview = library.locator('.library-card-preview');
    const portrait = async (card: Locator) => {
      const ratio = await card.evaluate(element => (element as HTMLElement).offsetWidth / (element as HTMLElement).offsetHeight);
      expect(ratio).toBeCloseTo(5 / 7, 2);
      const title = card.locator('.card-face-copy strong');
      await expect(title).toBeVisible();
      expect(await title.evaluate(element => element.scrollHeight <= element.clientHeight + 1)).toBe(true);
      // One physical body and one soft shadow: no offset paper-coloured copies below it.
      await expect(card.locator(':scope > .card-face')).toHaveCount(1);
      await expect(card).toHaveCSS('outline-style', 'none');
      const shadows = await card.evaluate(element => getComputedStyle(element).boxShadow.split(/,(?![^(]*\))/)
        .map(shadow => shadow.replace(/rgba?\([^)]+\)/g, '').trim().split(/\s+/).map(parseFloat)));
      expect(shadows).toHaveLength(1);
      expect(shadows[0][2]).toBeGreaterThan(0);
    };
    for (const [id, label, illustration] of [
      ['agent', 'Agent', 'agent'], ['oaw.barracks.summoner', 'Summoning', 'summoning'], ['sandbox', 'Sandbox', 'sandbox'],
    ]) {
      const source = library.locator(`[data-library-card="${id}"]`);
      const border = await source.locator('.card-stock').evaluate(element => getComputedStyle(element).borderColor);
      await source.click();
      await page.mouse.move(1, 1);
      await expect(source.locator('.card-stock')).toHaveCSS('border-color', border);
      await expect(source.locator(`[data-illustration="${illustration}"]`)).toBeVisible();
      await expect(preview.locator(`[data-illustration="${illustration}"]`)).toBeVisible();
      await expect(source.locator('.card-print-art, .card-face-detail, .card-face-badge, .card-face-copy small')).toHaveCount(0);
      await expect(library.getByRole('complementary', { name: 'Card details' })).toContainText(definitions[id].description);
      await expect(source.locator('.card-face')).toHaveText((await preview.locator('.card-face').textContent())!);
      await portrait(source.locator('.card-stock'));
      await portrait(preview);
      // Compare actual printed geometry, including the variants inherited from legacy catalogs.
      const geometry = (card: Locator) => card.locator('.card-face').evaluate(element => {
        const face = element.getBoundingClientRect();
        return ['.card-face-edition', '.card-face-art', '.card-face-copy'].flatMap(selector => {
          const box = element.querySelector(selector)!.getBoundingClientRect();
          return [(box.x - face.x) / face.width, (box.y - face.y) / face.height, box.width / face.width, box.height / face.height];
        });
      });
      const thumbnailGeometry = await geometry(source);
      (await geometry(preview)).forEach((value, index) => expect(value).toBeCloseTo(thumbnailGeometry[index], 2));
      await page.screenshot({ path: info.outputPath(`library-light-${illustration}.png`), animations: 'disabled' });
      await preview.screenshot({ path: info.outputPath(`card-${illustration}.png`), animations: 'disabled' });
      await library.getByRole('button', { name: `Add ${label} to deck`, exact: true }).click();
    }
    // Selection lives outside the print; pointer hover cannot replace the illustration.
    await expect(library.locator('.library-card.is-selected [data-library-card="sandbox"]')).toBeVisible();
    await preview.hover({ position: { x: 30, y: 30 } });
    await expect(preview.locator('[data-illustration="sandbox"]')).toBeVisible();
    await portrait(preview);
    await page.mouse.move(1, 1);
    await library.getByRole('button', { name: 'Close Library' }).click();
    await page.getByRole('button', { name: 'Use dark theme', exact: true }).click();
    await open.click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await library.locator('[data-library-card="agent"]').click();
    await page.mouse.move(1, 1);
    await page.screenshot({ path: info.outputPath('library-dark.png'), animations: 'disabled' });
    for (const viewport of [{ width: 1280, height: 800 }, { width: 720, height: 650 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      await library.locator('[data-library-card="oaw.barracks.summoner"]').click();
      await page.mouse.move(1, 1);
      await portrait(preview);
      expect(await library.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
      expect(await library.locator('.library-card-collection').evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
      await page.screenshot({ path: info.outputPath(`library-dark-${viewport.width}.png`), animations: 'disabled' });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await library.getByRole('button', { name: 'Remove Summoning from deck', exact: true }).click();
    const source = library.locator('[data-library-card="oaw.barracks.summoner"]');
    await expect(source).toHaveAttribute('data-can-drag', 'true');
    await source.scrollIntoViewIfNeeded();
    const box = (await source.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 14, box.y + box.height / 2, { steps: 3 });
    await expect(page.locator('.palette-drag-preview')).toBeVisible();
    const deck = page.getByRole('complementary', { name: 'Active card deck' });
    const target = (await deck.locator('.deck-stage').boundingBox())!;
    await page.mouse.move(target.x + target.width * .7, target.y + target.height / 2, { steps: 12 });
    await page.mouse.up();
    await expect(library.getByRole('button', { name: 'Remove Summoning from deck', exact: true })).toBeVisible();
    await library.getByRole('button', { name: 'Close Library' }).click();
    await page.reload();
    await deck.hover();
    for (const label of ['Agent', 'Summoning', 'Sandbox']) {
      const card = deck.getByRole('button', { name: `Place ${label}`, exact: true });
      await expect(card.locator('[data-design="archival"]')).toBeVisible();
      await portrait(card.locator('.card-stock'));
    }
    await page.mouse.move(1, 1);
    await deck.hover();
    await page.screenshot({ path: info.outputPath('deck-dark.png'), animations: 'disabled' });
    // Place a collected card using the existing action and retain its identity after reload.
    const placed = page.waitForResponse(response => response.url().endsWith('/api/card-library/nodes') && response.request().method() === 'POST');
    await deck.getByRole('button', { name: 'Place Agent', exact: true }).click();
    const response = await placed;
    expect(response.status()).toBe(201);
    placedId = (await response.json()).id;
    await expect(page.locator(`.world-card[data-card-id="${placedId}"][data-card-type="agent"]`)).toBeVisible();
    expect((await read()).card_definitions).toEqual(definitions);
    expect(errors).toEqual([]);
  } finally {
    if (placedId) await request.delete(`/api/nodes/${placedId}`);
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

test('keeps the installed Codex logo in the collection, enlarged print and hand', async ({ page, request }, info) => {
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw.locale': 'en', 'oaw-theme': 'dark',
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
    },
  } })).ok()).toBe(true);
  const read = async (): Promise<LibrarySnapshot> => (await request.get('/api/card-library')).json();
  let state = await read();
  const codex = state.card_definitions['openai.codex.agent'];
  expect(codex.icon_url).toBeTruthy();
  expect((await request.get(codex.icon_url!)).ok()).toBe(true);
  const pack = Object.values(state.packs).find(item => item.definition.cards.includes(codex.id))!;
  const originalDeck = state.active_deck_id;
  if (!pack.opened) {
    const opened = await request.post('/api/card-library/actions', { data: {
      action: 'open_pack', id: pack.definition.id, expected_revision: state.revision,
    } });
    expect(opened.ok()).toBe(true);
    state = await opened.json();
  }
  const created = await request.post('/api/card-library/actions', { data: {
    action: 'create_deck', name: 'Branded card study', entries: [{ kind: 'node', id: codex.id }], expected_revision: state.revision,
  } });
  expect(created.ok()).toBe(true);
  const studyDeck = (await created.json() as LibrarySnapshot).active_deck_id;
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto('/');
    await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
    const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
    await library.getByRole('button', { name: /^Cards/ }).click();
    await library.getByLabel('Source pack', { exact: true }).selectOption(`pack:${pack.definition.id}`);
    const thumbnail = library.locator(`[data-library-card="${codex.id}"]`);
    await thumbnail.click();
    for (const card of [thumbnail, library.locator('.library-card-preview')]) {
      const logo = card.locator('.catalog-asset-icon');
      await expect(logo).toBeVisible();
      expect(await logo.evaluate(element => getComputedStyle(element).maskImage)).toContain(codex.icon_url);
      await expect(card.locator('[data-illustration]')).toHaveCount(0);
    }
    await page.mouse.move(1, 1);
    await page.screenshot({ path: info.outputPath('codex-brand-library.png'), animations: 'disabled' });
    await library.getByRole('button', { name: 'Close Library' }).click();
    const deck = page.getByRole('complementary', { name: 'Active card deck' });
    await deck.hover();
    await expect(deck.locator(`[data-palette-card="${codex.id}"] .catalog-asset-icon`)).toBeVisible();
    await expect(deck.locator('[data-illustration]')).toHaveCount(0);
    await page.screenshot({ path: info.outputPath('codex-brand-deck.png'), animations: 'disabled' });
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
