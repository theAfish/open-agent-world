import { readFile } from 'node:fs/promises';
import { expect, test, type Page } from '@playwright/test';

const publishUrl = 'https://open-agent-world.space/marketplace/publish/new';
const nodes: string[] = [], legions: string[] = [];

test.beforeEach(async ({ page, request }) => {
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }) } } });
  await page.setViewportSize({ width: 1700, height: 1100 });
});
test.afterEach(async ({ request }) => {
  if (nodes.length) await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.splice(0) } });
  for (const id of legions.splice(0)) await request.delete(`/api/legions/${id}`);
});

async function downloadedPack(page: Page, click: () => Promise<void>) {
  const downloaded = page.waitForEvent('download');
  await click();
  const file = await downloaded;
  expect(await file.failure()).toBeNull();
  const bytes = await readFile((await file.path())!);
  expect(bytes.subarray(0, 4).toString('hex')).toBe('504b0304');
  expect(bytes.length).toBeGreaterThan(100);
  return file.suggestedFilename();
}

test('saved Legion exports offline, then hands off only a bare URL after a new namespace export', async ({ page, request, context }, testInfo) => {
  const source = await (await request.post('/api/nodes', { data: { type: 'text', name: 'Publish example' } })).json();
  const partner = await (await request.post('/api/nodes', { data: { type: 'text', name: 'Partner note', position: { x: 400, y: 200 } } })).json();
  nodes.push(source.id, partner.id);
  const saved = await request.post('/api/legions', { data: { name: 'Publish example', node_ids: [source.id, partner.id] } });
  expect(saved.ok()).toBe(true);
  const legion = await saved.json();
  legions.push(legion.id);
  const external: { method: string; url: string; body: string | null; headers: Record<string, string> }[] = [];
  await context.route('https://open-agent-world.space/**', route => {
    const req = route.request();
    external.push({ method: req.method(), url: req.url(), body: req.postData(), headers: req.headers() });
    return route.fulfill({ contentType: 'text/html', body: '<h1>Marketplace submission fixture</h1>' });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  await library.getByRole('button', { name: /^Cards/ }).click();
  await library.getByRole('textbox', { name: 'Search cards' }).fill('Publish example');
  await library.getByRole('button', { name: 'Inspect Publish example', exact: true }).click();
  await library.getByRole('button', { name: 'Make a Pack…' }).click();
  const creator = library.locator('.pack-creator');
  const id = creator.getByLabel('Pack ID', { exact: true });
  await expect(id).toHaveValue(/^local\./);
  await creator.getByRole('button', { name: 'Check Pack', exact: true }).click();
  const localName = await downloadedPack(page, () => creator.getByRole('button', { name: 'Export .oawpack' }).click());
  expect(localName).toMatch(/^local\..*\.oawpack$/);
  await expect(creator.getByRole('button', { name: 'Publish on Marketplace' })).toBeDisabled();
  expect(external).toEqual([]);

  await id.fill('my_team.invalid');
  expect(await id.evaluate((input: HTMLInputElement) => input.checkValidity())).toBe(false);
  await id.fill('my-team.my-pack');
  expect(await id.evaluate((input: HTMLInputElement) => input.checkValidity())).toBe(true);
  await creator.getByRole('button', { name: 'Check Pack', exact: true }).click();
  expect(await downloadedPack(page, () => creator.getByRole('button', { name: 'Export .oawpack' }).click())).toBe('my-team.my-pack-0.1.0.oawpack');
  expect(external).toEqual([]);
  const publish = creator.getByRole('link', { name: 'Publish on Marketplace' });
  await expect(publish).toHaveAttribute('href', publishUrl);
  await expect(creator).toContainText('Submission enters review');
  // Simulate unavailable navigation: the address remains usable without a popup handle.
  await publish.evaluate(link => link.addEventListener('click', event => event.preventDefault(), { once: true }));
  await publish.click();
  expect(external).toEqual([]);
  await expect(creator.locator('code').filter({ hasText: publishUrl })).toBeVisible();
  await creator.screenshot({ path: testInfo.outputPath('pack-publishing.png') });
  const popupEvent = page.waitForEvent('popup');
  await publish.click();
  const popup = await popupEvent;
  await expect(popup).toHaveURL(publishUrl);
  await expect(popup.getByRole('heading')).toHaveText('Marketplace submission fixture');
  expect(await popup.evaluate(() => window.opener)).toBeNull();
  expect(external).toHaveLength(1);
  expect(external[0]).toMatchObject({ method: 'GET', url: publishUrl, body: null });
  for (const header of ['authorization', 'referer', 'x-oaw-pack-install', 'x-oaw-instance']) expect(external[0].headers[header]).toBeUndefined();
  await popup.close();
});

test('Factory saves a namespace ID in its designer and exports through its existing packer', async ({ page, request }) => {
  const source = await (await request.post('/api/nodes', { data: { type: 'text', name: 'Factory example' } })).json();
  const design = await (await request.post('/api/nodes', { data: { type: 'oaw.factory.pack', position: { x: 100, y: 200 } } })).json();
  const packer = await (await request.post('/api/nodes', { data: { type: 'oaw.factory.packer', position: { x: 1200, y: 200 },
    config: { items: [{ kind: 'node', id: source.id, name: source.name }], params: {}, include_content: false } } })).json();
  nodes.push(source.id, design.id, packer.id);
  expect((await request.post('/api/edges', { data: { source: design.id, target: packer.id, relationship: 'oaw.factory.pack-input' } })).ok()).toBe(true);
  await page.goto('/');
  async function open(id: string) {
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    await page.locator(`[data-card-id="${id}"]`).click({ position: { x: 80, y: 25 } });
    const editor = page.locator(`[data-workspace-node-id="${id}"]`);
    await expect(editor).toBeVisible();
    // Match the Factory suite: focus after React Flow measures the opened workspace.
    await page.waitForTimeout(300);
    await page.keyboard.press('f');
    await expect.poll(async () => (await editor.boundingBox())!.width).toBeLessThan(1600);
    await page.waitForTimeout(450);
    return editor;
  }
  const designer = await open(design.id);
  await designer.getByLabel('Pack ID', { exact: true }).fill('my-team.factory-cards');
  await expect(designer).toContainText('Author names do not grant permission');
  await designer.getByRole('button', { name: '保存设计', exact: true }).click();
  await expect(designer.locator('.factory-message')).toContainText('设计已保存');
  await designer.getByRole('button', { name: 'Close workspace', exact: true }).click();
  const editor = await open(packer.id);
  // Export also saves changed options; this must not invalidate its own export receipt.
  await editor.getByRole('checkbox').check();
  expect(await downloadedPack(page, () => editor.getByRole('button', { name: '导出 .oawpack' }).click())).toBe('my-team.factory-cards-0.1.0.oawpack');
  await expect(editor.getByRole('link', { name: 'Publish on Marketplace' })).toHaveAttribute('href', publishUrl);
});
