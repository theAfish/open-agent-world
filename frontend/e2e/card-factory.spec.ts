import { expect, test, type Page } from '@playwright/test';
import { FACE_MODES, presetSurface } from '../src/factory/faceDesign';
import { chooseRecipe, newSlot } from '../src/factory/designRecipes';
import type { FaceDesign } from '../src/factory/types';
test.use({ actionTimeout: 10000 });

const createdNodes: string[] = [];
const createdLegions: string[] = [];
test.afterEach(async ({ request }) => {
  const ids = createdNodes.splice(0);
  if (ids.length) await request.post('/api/nodes/batch-delete', { data: { node_ids: ids } });
  for (const id of createdLegions.splice(0)) await request.delete(`/api/legions/${id}`);
});

async function openDevice(page: Page, id: string) {
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  const card = page.locator(`[data-card-id="${id}"]`);
  await card.click({ position: { x: 80, y: 25 } });
  const dialog = page.locator(`[data-workspace-node-id="${id}"]`);
  await expect(dialog).toBeVisible();
  await expect(card).toHaveCSS('width', '1020px');
  // The 240ms workspace transition precedes React Flow's measured bounds.
  await page.waitForTimeout(300);
  await page.keyboard.press('f');
  await expect.poll(async () => (await dialog.boundingBox())!.width).toBeLessThan(1600);
  await page.waitForTimeout(450); // Wait for camera focus before raw pointer-coordinate gestures.
  return dialog;
}

test('design, try, print, pack and download real cards through the factory', async ({ page, request }, testInfo) => {
  test.setTimeout(90000);
  await page.setViewportSize({ width: 1700, height: 1100 });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }) } } });
  const response = await request.post('/api/legions/presets/oaw.factory.workshop/instances', { data: { position: { x: 400, y: 300 } } });
  expect(response.ok()).toBe(true);
  const workshop = await response.json();
  createdNodes.push(...workshop.nodes.map((node: { id: string }) => node.id));
  const devices: Record<string, string> = Object.fromEntries(workshop.nodes.map((node: { type: string; id: string }) => [node.type.split('.').pop(), node.id]));
  await page.goto('/');
  const face = await openDevice(page, devices.face);
  await expect(face.locator('.face-properties')).toHaveCount(0);
  await expect(face.getByRole('spinbutton')).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath('face-designer-layouts.png') });
  await face.getByRole('button', { name: '应用 Hero 版式', exact: true }).click();
  await face.getByRole('button', { name: '风格', exact: true }).click();
  await face.getByRole('button', { name: '应用 Playful 风格', exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath('face-designer-styles.png') });
  await face.getByRole('button', { name: '内容', exact: true }).click();
  await face.getByLabel('卡牌名称', { exact: true }).fill('问候卡');
  await face.getByRole('button', { name: '材质', exact: true }).click();
  await face.getByRole('button', { name: 'Starlight 材质', exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath('face-designer-material.png') });
  await expect(face.locator('.face-edit-stage .factory-artwork')).toHaveAttribute('data-kit', 'playful');
  await face.getByRole('button', { name: '保存设计', exact: true }).click();
  await expect(face.locator('.factory-message')).toContainText('设计已保存');
  await page.screenshot({ path: testInfo.outputPath('face-designer.png') });
  await face.getByRole('button', { name: 'Close workspace', exact: true }).click();

  const func = await openDevice(page, devices.function);
  await func.getByRole('textbox', { name: '输出模板', exact: true }).fill('欢迎 {{name}} 来到卡包工厂');
  await func.locator('.factory-test').getByLabel('名称', { exact: true }).fill('小明');
  await func.getByRole('button', { name: '试运行', exact: true }).click();
  await expect(func.locator('output')).toHaveText('欢迎 小明 来到卡包工厂');
  await func.getByRole('button', { name: '保存设计', exact: true }).click();
  await expect(func.locator('.factory-message')).toContainText('设计已保存');
  await page.screenshot({ path: testInfo.outputPath('function-designer.png') });
  await func.getByRole('button', { name: 'Close workspace', exact: true }).click();

  const printer = await openDevice(page, devices.printer);
  await expect(printer.getByRole('button', { name: '印刷卡牌', exact: true })).toBeEnabled();
  const printing = page.waitForResponse(response => response.url().endsWith(`/factory/${devices.printer}/print`) && response.request().method() === 'POST');
  await printer.getByRole('button', { name: '印刷卡牌', exact: true }).click();
  await expect(printer.getByRole('status')).toContainText('已印刷');
  const printed = await (await printing).json();
  createdNodes.push(printed.id);
  expect(printed.name).toBe('问候卡');
  await printer.getByRole('button', { name: 'Close workspace', exact: true }).click();
  const runtime = await openDevice(page, printed.id);
  await runtime.getByLabel('名称', { exact: true }).fill('世界');
  await runtime.getByRole('button', { name: '运行', exact: true }).click();
  await expect(runtime.locator('output')).toHaveText('欢迎 世界 来到卡包工厂');
  const authored = page.locator(`[data-card-id="${printed.id}"]`);
  await authored.getByRole('combobox', { name: '显示模式', exact: true }).selectOption('node');
  await expect(authored).toHaveCSS('width', '112px');
  await expect(authored.locator('.factory-shapes ellipse')).toHaveCount(1);
  await authored.hover();
  await authored.getByRole('combobox', { name: '显示模式', exact: true }).selectOption('inspector');
  await expect(authored).toHaveCSS('width', '438px');
  await expect(page.locator(`[data-resize-node="${printed.id}"]`)).toHaveCount(0);
  await expect(authored.getByLabel('名称', { exact: true })).toBeVisible();
  await authored.getByRole('combobox', { name: '显示模式', exact: true }).selectOption('workspace');
  await runtime.getByRole('button', { name: 'Close workspace', exact: true }).click();

  const packer = await openDevice(page, devices.packer);
  await packer.getByRole('combobox', { name: '选择素材', exact: true }).selectOption(`node:${printed.id}`);
  await packer.getByRole('button', { name: '加入', exact: true }).click();
  await expect(packer.locator('.factory-dropzone li')).toContainText('问候卡');
  await packer.getByRole('button', { name: '添加参数', exact: true }).click();
  await packer.getByLabel('参数标识', { exact: true }).fill('name');
  await packer.getByLabel('参数值', { exact: true }).fill('朋友');
  const download = page.waitForEvent('download');
  await packer.getByRole('button', { name: '导出 .oawpack', exact: true }).click();
  const artifact = await download;
  expect(artifact.suggestedFilename()).toBe('local.mycards-0.1.0.oawpack');
  await artifact.saveAs(testInfo.outputPath(artifact.suggestedFilename()));
  await expect(packer.getByRole('button', { name: '分享文件', exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('packer.png') });
  await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
  await expect(packer.locator('.factory').first()).toHaveCSS('color', 'rgb(233, 230, 222)');
  await page.screenshot({ path: testInfo.outputPath('packer-dark.png') });
  await page.reload();
  await expect(page.locator(`[data-factory-packer="${devices.packer}"] li`)).toContainText('问候卡');
  expect(errors).toEqual([]);
});

test('dragging a canvas card and a Legion into the packer keeps their sources', async ({ page, request }) => {
  await page.setViewportSize({ width: 1500, height: 1000 });
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw-canvas-viewport-v1': JSON.stringify({ state: { viewport: { x: 0, y: 0, zoom: 1, width: 1500, height: 1000 } }, version: 0 }) } } });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const make = async (type: string, name: string, x: number) => {
    const response = await request.post('/api/nodes', { data: { type, name, position: { x, y: 450 } } });
    expect(response.ok()).toBe(true);
    const card = await response.json(); createdNodes.push(card.id); return card;
  };
  const source = await make('text', 'Source note', 400), partner = await make('text', 'Partner note', 750);
  const packer = await make('oaw.factory.packer', 'Drop packer', 1200);
  const legionResponse = await request.post('/api/legions', { data: { name: 'Saved formation', node_ids: [source.id, partner.id] } });
  expect(legionResponse.ok()).toBe(true);
  const legion = await legionResponse.json(); createdLegions.push(legion.id);
  await page.goto('/');
  const zone = page.locator(`[data-factory-packer="${packer.id}"]`);
  await expect(zone).toBeVisible();
  const origin = (await page.locator(`[data-card-id="${source.id}"]`).boundingBox())!;
  const target = (await zone.boundingBox())!;
  await page.mouse.move(origin.x + 60, origin.y + 25);
  await page.mouse.down();
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 20 });
  await page.mouse.up();
  await expect.poll(async () => (await (await request.get(`/api/nodes/${packer.id}`)).json()).config.items.length).toBe(1);
  const remaining = await (await request.get(`/api/nodes/${source.id}`)).json();
  expect(remaining.position).toEqual(source.position);
  expect(remaining.parent_id).toBeNull();
  const dataTransfer = await page.evaluateHandle(({ id, revision }) => {
    const data = new DataTransfer();
    data.setData('application/vnd.open-agent-world.palette-item+json', JSON.stringify({ version: 1, kind: 'legion', id, revision }));
    return data;
  }, legion);
  const destination = (await zone.boundingBox())!;
  await zone.dispatchEvent('drop', { dataTransfer, clientX: destination.x + destination.width / 2, clientY: destination.y + destination.height / 2 });
  await expect.poll(async () => (await (await request.get(`/api/nodes/${packer.id}`)).json()).config.items.length).toBe(2);
  expect((await (await request.get('/api/legions')).json()).some((item: { id: string }) => item.id === legion.id)).toBe(true);
  expect(errors).toEqual([]);
});

test('interactive face modes, snapping, polygon editing and PNG survive printing and reload', async ({ page, request }, testInfo) => {
  test.setTimeout(90000);
  await page.setViewportSize({ width: 1700, height: 1100 });
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-canvas-viewport-v1': JSON.stringify({ state: { viewport: { x: 0, y: 0, zoom: 1, width: 1700, height: 1100 } }, version: 0 }) } } });
  const workshop = await (await request.post('/api/legions/presets/oaw.factory.workshop/instances', { data: { position: { x: 400, y: 300 } } })).json();
  createdNodes.push(...workshop.nodes.map((node: { id: string }) => node.id));
  const devices = Object.fromEntries(workshop.nodes.map((node: { id: string; type: string }) => [node.type.split('.').pop(), node.id]));
  await page.goto('/');
  const editor = await openDevice(page, devices.face);
  await editor.getByRole('button', { name: '内容', exact: true }).click();
  await editor.getByLabel('卡牌名称', { exact: true }).fill('自由形状卡');
  await editor.getByRole('button', { name: '高级', exact: true }).click();
  await editor.locator('.face-layer-list').getByRole('button', { name: '标题 槽位', exact: true }).click();
  await editor.getByRole('spinbutton', { name: '位置 X', exact: true }).fill('24');
  await editor.getByRole('spinbutton', { name: '位置 Y', exact: true }).fill('104');
  await editor.getByRole('spinbutton', { name: '位置 Y', exact: true }).press('Enter');
  const stage = editor.getByRole('group', { name: '卡片模式设计画布' });
  const title = stage.locator('[data-edit-element="title"]');
  const bounds = (await title.boundingBox())!, canvas = (await stage.boundingBox())!;
  const scale = canvas.width / 240;
  await page.keyboard.down('Alt');
  await page.mouse.move(bounds.x + 12 * scale, bounds.y + 20 * scale); await page.mouse.down();
  await page.mouse.move(bounds.x + 27 * scale, bounds.y + 42 * scale, { steps: 8 }); await page.mouse.up();
  await page.keyboard.up('Alt');
  await expect.poll(async () => Number(await editor.getByRole('spinbutton', { name: '位置 X', exact: true }).inputValue())).toBeCloseTo(39, 0);
  await expect.poll(async () => Number(await editor.getByRole('spinbutton', { name: '位置 Y', exact: true }).inputValue())).toBeCloseTo(126, 0);
  await stage.focus(); await page.keyboard.press('ArrowRight');
  await expect.poll(async () => Number(await editor.getByRole('spinbutton', { name: '位置 X', exact: true }).inputValue())).toBeCloseTo(40, 0);
  await editor.getByRole('button', { name: '撤销', exact: true }).click();
  await expect.poll(async () => Number(await editor.getByRole('spinbutton', { name: '位置 X', exact: true }).inputValue())).toBeCloseTo(39, 0);
  const moved = (await title.boundingBox())!;
  await page.mouse.move(moved.x + 10 * scale, moved.y + 10 * scale); await page.mouse.down();
  await page.mouse.move(moved.x - 3 * scale, moved.y + 10 * scale, { steps: 8 });
  await expect(stage.locator('.face-snap-guide.is-x')).toBeVisible(); await page.mouse.up();
  // The new recipe's safe margin is the alignment anchor.
  expect(Number(await editor.getByRole('spinbutton', { name: '位置 X', exact: true }).inputValue())).toBe(23);

  await editor.getByRole('button', { name: '卡片', exact: true }).click();
  await editor.getByRole('button', { name: '应用 Minimal 版式', exact: true }).click();
  await expect(stage.locator('.factory-artwork')).toHaveAttribute('data-recipe', 'minimal');
  await editor.getByRole('button', { name: '撤销', exact: true }).click();
  await expect(stage.locator('.factory-artwork')).toHaveAttribute('data-recipe', 'hero');
  await editor.getByRole('button', { name: '高级', exact: true }).click();
  await editor.getByRole('spinbutton', { name: '画布宽度', exact: true }).fill('360');
  await editor.getByRole('spinbutton', { name: '画布高度', exact: true }).fill('240');
  await editor.getByRole('button', { name: '编辑形状', exact: true }).click();
  await editor.getByRole('button', { name: '＋多边形', exact: true }).click();
  const vertex = stage.getByRole('button', { name: '顶点 1', exact: true });
  const vertexBounds = (await vertex.boundingBox())!, newScale = (await stage.boundingBox())!.width / 360;
  await page.mouse.move(vertexBounds.x + vertexBounds.width / 2, vertexBounds.y + vertexBounds.height / 2); await page.mouse.down();
  await page.mouse.move(vertexBounds.x + vertexBounds.width / 2 + 24 * newScale, vertexBounds.y + vertexBounds.height / 2 + 16 * newScale, { steps: 8 }); await page.mouse.up();
  await editor.getByRole('button', { name: '＋椭圆', exact: true }).click();
  // Remove the full rectangle so the rendered boundary is the union of our two shapes.
  await editor.locator('.face-layer-list').getByRole('button', { name: '圆角矩形', exact: true }).click();
  await editor.getByRole('button', { name: '删除', exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath('shape-editor.png') });
  await editor.getByText('背景与 PNG 轮廓', { exact: true }).click();
  const png = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 360; canvas.height = 240;
    const ctx = canvas.getContext('2d')!; ctx.fillStyle = '#cbded5'; ctx.beginPath(); ctx.ellipse(180, 120, 176, 116, 0, 0, Math.PI * 2); ctx.fill();
    return canvas.toDataURL('image/png').split(',')[1];
  });
  await editor.getByLabel('上传 PNG 背景', { exact: true }).setInputFiles({ name: 'oval.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64') });
  await expect(stage.locator('.factory-artwork')).toHaveAttribute('data-image-shape', 'true');
  await editor.getByRole('button', { name: '节点 已启用', exact: true }).click();
  await editor.getByLabel('允许此显示模式', { exact: true }).uncheck();
  await editor.getByRole('button', { name: '详细 已启用', exact: true }).click();
  await editor.getByLabel('允许此显示模式', { exact: true }).uncheck();
  await editor.getByRole('button', { name: '工作区 已启用', exact: true }).click();
  await editor.getByRole('spinbutton', { name: '画布宽度', exact: true }).fill('800');
  await editor.getByRole('spinbutton', { name: '画布高度', exact: true }).fill('560');
  await editor.getByRole('button', { name: '卡片 已启用', exact: true }).click();
  await expect(editor.getByRole('spinbutton', { name: '画布宽度', exact: true })).toHaveValue('360');
  await editor.getByRole('button', { name: '保存设计', exact: true }).click();
  await expect(editor.locator('.factory-message')).toContainText('设计已保存');
  await page.screenshot({ path: testInfo.outputPath('png-designer.png') });
  const saved = (await (await request.get(`/api/nodes/${devices.face}`)).json()).config.studio;
  expect(saved.enabled).toEqual(['preview', 'workspace']);
  expect(saved.modes.preview.shapes.map((shape: { kind: string }) => shape.kind)).toEqual(['polygon', 'ellipse']);
  expect(saved.modes.preview.shapes[0].points[0]).toEqual({ x: .65, y: .1 });
  expect(saved.modes.preview.background_png).toBe(`data:image/png;base64,${png}`);
  expect(saved.modes.workspace.background_png).toBe('');
  await editor.getByRole('button', { name: 'Close workspace', exact: true }).click();
  const result = await request.post(`/api/packs/factory/${devices.printer}/print`, { headers: { 'X-OAW-Pack-Install': '1' } });
  expect(result.ok()).toBe(true); const printed = await result.json(); createdNodes.push(printed.id);
  await page.reload(); await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  const runtime = page.locator(`[data-card-id="${printed.id}"]`);
  await expect(runtime).toHaveCSS('width', '360px'); await expect(runtime).toHaveCSS('height', '240px');
  await runtime.click({ position: { x: 160, y: 130 } });
  await expect(runtime).toHaveAttribute('data-surface-level', 'workspace');
  await expect(runtime).toHaveCSS('width', '800px');
  await page.waitForTimeout(400); await page.keyboard.press('f');
  await runtime.getByLabel('名称', { exact: true }).fill('自定义卡面');
  await runtime.getByRole('button', { name: '运行', exact: true }).click();
  await expect(runtime.locator('output')).toHaveText('你好，自定义卡面！');
  expect(await runtime.getByRole('combobox', { name: '显示模式', exact: true }).locator('option').allTextContents()).toEqual(['卡片', '工作区']);
  await runtime.getByRole('combobox', { name: '显示模式', exact: true }).selectOption('preview');
  await expect(runtime).toHaveCSS('width', '360px');
  await expect(runtime.locator('.factory-artwork')).toHaveAttribute('data-image-shape', 'true');
  await page.screenshot({ path: testInfo.outputPath('printed-png-card.png') });
  await page.reload(); await expect(runtime).toHaveAttribute('data-surface-level', 'preview');
  await expect(runtime.locator('.factory-background')).toHaveAttribute('src', `data:image/png;base64,${png}`);
  expect(errors).toEqual([]);
});

test('semantic designer reflows content, reflects light and restores the saved style', async ({ page, request }, testInfo) => {
  test.setTimeout(60000);
  await page.setViewportSize({ width: 1700, height: 1100 });
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-canvas-viewport-v1': JSON.stringify({ state: { viewport: { x: 0, y: 0, zoom: 1, width: 1700, height: 1100 } }, version: 0 }) } } });
  const created = await request.post('/api/legions/presets/oaw.factory.workshop/instances', { data: { position: { x: 400, y: 300 } } });
  expect(created.ok()).toBe(true); const workshop = await created.json();
  createdNodes.push(...workshop.nodes.map((node: { id: string }) => node.id));
  const card = workshop.nodes.find((node: { type: string }) => node.type === 'oaw.factory.face');
  await page.goto('/'); await expect(page.locator(`[data-card-id="${card.id}"]`)).toBeVisible();
  const editor = await openDevice(page, card.id);
  await editor.getByRole('button', { name: '应用 Badge 版式' }).click();
  await editor.getByRole('button', { name: '风格', exact: true }).click();
  await editor.getByRole('button', { name: '应用 Sand 风格' }).click();
  await editor.getByRole('button', { name: '内容', exact: true }).click();
  const sidebar = editor.locator('.face-sidebar');
  await sidebar.getByLabel('卡牌名称', { exact: true }).fill('灵感收藏夹');
  await sidebar.getByLabel('说明', { exact: true }).fill('收集闪现的念头，让好想法慢慢生长。');
  await sidebar.locator('.face-slot-add').getByRole('button', { name: '标签', exact: true }).click();
  await sidebar.getByLabel('标签', { exact: true }).fill('灵感, 日常');
  await expect(editor.getByRole('spinbutton')).toHaveCount(0);
  await editor.getByRole('button', { name: '关闭内容检查器' }).click();
  await editor.getByRole('button', { name: '设计标题', exact: true }).click();
  await page.keyboard.press('Delete');
  await expect(editor.locator('.face-edit-stage [data-kind=title]')).toHaveCount(0);
  await editor.getByRole('button', { name: '撤销', exact: true }).click();
  await expect(editor.locator('.face-edit-stage [data-kind=title]')).toHaveText('灵感收藏夹');
  await sidebar.locator('.face-panel-scroll').evaluate(el => el.scrollTop = 0);
  await editor.locator('.factory-face-editor').screenshot({ path: testInfo.outputPath('designer-content.png') });
  await editor.getByRole('button', { name: '材质', exact: true }).click();
  await editor.getByRole('button', { name: 'Holo 材质' }).click();
  await editor.getByRole('slider', { name: '材质强度' }).fill('65');
  await editor.getByRole('button', { name: '润色', exact: true }).click();
  await expect(editor.locator('.face-studio-footer')).toContainText('材质强度');
  await expect(editor.getByRole('slider', { name: '材质强度' })).toHaveValue('55');
  await editor.getByRole('button', { name: '撤销', exact: true }).click();
  await expect(editor.getByRole('slider', { name: '材质强度' })).toHaveValue('65');
  await sidebar.locator('.face-panel-scroll').evaluate(el => el.scrollTop = 0);
  await editor.locator('.factory-face-editor').screenshot({ path: testInfo.outputPath('designer-light.png') });
  await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
  await expect(editor.locator('.factory-face-editor')).toHaveCSS('background-color', 'rgb(44, 46, 43)');
  await editor.locator('.factory-face-editor').screenshot({ path: testInfo.outputPath('designer-dark.png') });
  await editor.getByRole('button', { name: '预览', exact: true }).click();
  const artwork = editor.locator('.face-edit-stage .factory-artwork'), material = artwork.locator('.card-finish-layer');
  await expect(material).toHaveAttribute('data-material-ready', '');
  const canvas = material.locator('canvas');
  const before = await canvas.evaluate(el => (el as HTMLCanvasElement).toDataURL());
  await artwork.hover({ position: { x: 60, y: 90 } });
  await expect(artwork).toHaveAttribute('data-finish-active', 'true');
  await expect.poll(() => canvas.evaluate(el => (el as HTMLCanvasElement).toDataURL())).not.toBe(before);
  await editor.locator('.factory-face-editor').screenshot({ path: testInfo.outputPath('designer-finished-preview.png') });
  await editor.getByRole('button', { name: '保存设计', exact: true }).click();
  await expect(editor.locator('.factory-message')).toContainText('设计已保存');
  const saved = (await (await request.get(`/api/nodes/${card.id}`)).json()).config.studio.modes.preview;
  expect(saved.design).toMatchObject({ recipe: 'badge', kit: 'sand', material: { type: 'holo', intensity: .65 } });
  expect(saved.elements.find((e: { kind: string }) => e.kind === 'tags').text).toBe('灵感, 日常');
  await page.reload();
  await expect(editor.locator('.face-edit-stage .factory-artwork')).toHaveAttribute('data-recipe', 'badge');
  await expect(editor.locator('.face-edit-stage .factory-artwork')).toHaveAttribute('data-kit', 'sand');
  await expect(editor.locator('.face-edit-stage [data-kind=title]')).toHaveText('灵感收藏夹');
  expect(errors).toEqual([]);
});

test('saved legacy views stay coherent and sample fields fit in all four modes', async ({ page, request }, testInfo) => {
  test.setTimeout(60000);
  await page.setViewportSize({ width: 1700, height: 1100 });
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-canvas-viewport-v1': JSON.stringify({ state: { viewport: { x: 0, y: 0, zoom: 1, width: 1700, height: 1100 } }, version: 0 }) } } });
  const created = await request.post('/api/legions/presets/oaw.factory.workshop/instances', { data: { position: { x: 400, y: 300 } } });
  expect(created.ok()).toBe(true); const workshop = await created.json();
  createdNodes.push(...workshop.nodes.map((node: { id: string }) => node.id));
  const card = workshop.nodes.find((node: { type: string }) => node.type === 'oaw.factory.face');
  const legacy: FaceDesign = { ...card.config, finish: 'starlight', title: '我的卡牌', description: '填写表单，生成你的内容。', icon: 'book-open' };
  const preview = presetSurface('preview', legacy);
  preview.elements.push(newSlot('fields'), newSlot('action'));
  const badge = chooseRecipe(preview, legacy, 'badge');
  badge.design!.kit = 'ink'; badge.design!.appearance = 'dark';
  legacy.studio = { version: 1, enabled: FACE_MODES, initial: 'preview', open: 'workspace',
    modes: { ...Object.fromEntries(FACE_MODES.map(mode => [mode, presetSurface(mode, legacy)])), preview: badge } };
  const patched = await request.patch(`/api/nodes/${card.id}`, { data: { config: legacy } });
  expect(patched.ok()).toBe(true);
  await page.goto('/'); await expect(page.locator(`[data-card-id="${card.id}"]`)).toBeVisible();
  const editor = await openDevice(page, card.id), stage = editor.locator('.face-edit-stage');
  await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
  await expect(editor.getByRole('heading', { name: '卡面设计器', exact: true })).toHaveCount(0);
  await expect(editor.locator('.face-properties')).toHaveCount(0);
  const checkFields = async () => {
    const fields = stage.locator('.factory-sample-fields');
    if (await fields.count()) {
      const bounds = (await fields.boundingBox())!;
      for (const child of await fields.locator('span').all()) {
        const item = (await child.boundingBox())!;
        expect(item.y + item.height).toBeLessThanOrEqual(bounds.y + bounds.height + 1);
        expect(await child.evaluate(el => el.scrollHeight - el.clientHeight)).toBeLessThanOrEqual(1);
      }
    }
  };
  for (const mode of FACE_MODES) {
    await editor.getByLabel('预览显示模式').selectOption(mode);
    await expect(stage.locator('.factory-artwork')).toHaveAttribute('data-kit', 'ink');
    await expect(stage.locator('.factory-material')).toHaveAttribute('data-restrained', 'true');
    await checkFields();
    await editor.screenshot({ path: testInfo.outputPath(`legacy-${mode}.png`) });
  }
  await editor.getByLabel('预览显示模式').selectOption('preview');
  await editor.getByRole('button', { name: '应用 Compact 版式' }).click();
  await editor.getByRole('button', { name: '风格', exact: true }).click();
  await editor.getByRole('button', { name: '应用 Sand 风格' }).click();
  await editor.getByRole('button', { name: '材质', exact: true }).click();
  await editor.getByRole('button', { name: 'Starlight 材质' }).click();
  await editor.getByRole('button', { name: '高级', exact: true }).click();
  for (const mode of FACE_MODES) {
    await editor.getByLabel('预览显示模式').selectOption(mode);
    await expect(stage.locator('.factory-artwork')).toHaveAttribute('data-kit', 'sand');
    await expect(stage.locator('.factory-artwork')).toHaveAttribute('data-recipe', 'compact');
    await checkFields();
    await editor.screenshot({ path: testInfo.outputPath(`refined-${mode}.png`) });
  }
  await editor.getByRole('button', { name: '保存设计', exact: true }).click();
  await expect(editor.locator('.factory-message')).toContainText('设计已保存');
  const saved = (await (await request.get(`/api/nodes/${card.id}`)).json()).config;
  for (const mode of FACE_MODES) expect(saved.studio.modes[mode].design).toMatchObject({ recipe: 'compact', kit: 'sand', material: { type: 'starlight' } });
  await page.reload();
  await editor.getByLabel('预览显示模式').selectOption('workspace');
  await expect(stage.locator('.factory-artwork')).toHaveAttribute('data-kit', 'sand');
});
