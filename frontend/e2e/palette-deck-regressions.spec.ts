import { expect, test } from '@playwright/test';

test('collect, move between decks, place and restore a strict plugin card through the actual palette', async ({ page, request }) => {
  test.setTimeout(80_000);
  await page.setViewportSize({ width: 1600, height: 1000 });
  const type = 'science.structure-viewer';
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const snapshot = async () => (await request.get('/api/card-library')).json();
  const nodes = async () => (await (await request.get('/api/nodes')).json()).filter((node: { type: string }) => node.type === type);
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  const pack = library.locator('article').filter({ has: page.getByRole('heading', { name: 'Structure viewer', exact: true }) });
  await pack.getByRole('button', { name: 'Open Pack', exact: true }).click();
  await library.getByRole('button', { name: 'Browse cards', exact: true }).click();
  await library.getByLabel('Search cards', { exact: true }).fill('Structure viewer');
  await library.getByRole('button', { name: 'Add Structure viewer to deck', exact: true }).click();
  await library.getByRole('button', { name: 'Close Library' }).click();
  const before = await snapshot();
  const source = before.decks.find((deck: { id: string }) => deck.id === before.active_deck_id);
  const tray = page.getByRole('complementary', { name: 'Active card deck' });
  await tray.getByRole('button', { name: 'Create a new card deck' }).click();
  await tray.getByLabel('Deck name', { exact: true }).fill('Structure research');
  await tray.getByRole('button', { name: 'Create deck', exact: true }).click();
  await expect(tray.getByRole('tab', { name: /Structure research/ })).toHaveAttribute('aria-selected', 'true');
  const targetId = (await snapshot()).active_deck_id;
  await tray.getByRole('tab', { name: new RegExp(source.name) }).click();
  const card = tray.getByRole('button', { name: 'Place Structure viewer', exact: true });
  await tray.hover();
  await card.dragTo(tray.getByRole('tab', { name: /Structure research/ }));
  await expect.poll(async () => {
    const state = await snapshot();
    return state.decks.find((deck: { id: string }) => deck.id === targetId).entries;
  }).toEqual([{ kind: 'node', id: type }]);
  const moved = await snapshot();
  expect(moved.decks.find((deck: { id: string }) => deck.id === source.id).entries.some((entry: { id: string }) => entry.id === type)).toBe(false);
  expect(await nodes()).toEqual([]);
  await expect(tray.getByRole('tab', { name: /Structure research/ })).toHaveAttribute('aria-selected', 'true');
  await page.reload();
  await tray.hover();
  await expect(card).toBeVisible();
  try {
    await card.click();
    await expect.poll(async () => (await nodes()).length).toBe(1);
    let created = (await nodes())[0];
    expect(created.status).toBe('ready');
    expect(created.config).toEqual({});
    await page.keyboard.press('Control+z');
    await expect.poll(async () => (await nodes()).length).toBe(0);
    await expect(page.getByText('Place Structure viewer undone', { exact: true })).toBeVisible();
    await page.keyboard.press('Control+Shift+z');
    await expect.poll(async () => (await nodes()).length).toBe(1);
    created = (await nodes())[0];
    expect(created.status).toBe('ready');
    expect(created.config).toEqual({});
    await tray.hover();
    await card.dragTo(page.locator('.react-flow__pane').first(), { targetPosition: { x: 350, y: 250 } });
    await expect.poll(async () => (await nodes()).length).toBe(2);
    expect((await nodes()).every((node: { status: string; config: object }) => node.status === 'ready' && Object.keys(node.config).length === 0)).toBe(true);
    await page.screenshot({ path: '../.outputs/palette-deck-regressions.png' });
    expect(errors).toEqual([]);
  } catch (error) {
    await page.screenshot({ path: '../.outputs/palette-deck-error.png' });
    throw error;
  } finally {
    for (const node of await nodes()) await request.delete(`/api/nodes/${node.id}`);
  }
});
