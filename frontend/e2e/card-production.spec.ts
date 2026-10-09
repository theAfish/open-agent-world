import { expect, test, type Page } from '@playwright/test';
import type { FaceDesign, SurfaceDesign } from '../src/factory/types';

const step = (page: Page, name: string) => page.getByRole('navigation', { name: '设计步骤' }).getByRole('button', { name: name === '工艺层' ? '选择油墨第 1 层' : name, exact: true }).click();
const addProcess = async (page: Page, name: string) => { await page.locator('.process-add-menu > summary').click(); await page.getByRole('button', { name, exact: true }).click(); };
const openPanel = async (page: Page, name: string) => page.locator('.process-element-panel > summary').filter({ hasText: name }).click();
const draft = async (page: Page): Promise<FaceDesign> => JSON.parse(await page.getByTestId('studio-recipe').textContent() ?? '{}');
const surface = async (page: Page): Promise<SurfaceDesign> => (await draft(page)).studio!.modes.preview!;
const geometry = (value: SurfaceDesign) => value.elements.map(({ id, x, y, width, height, font_size }) => ({ id, x, y, width, height, font_size }));

// The standalone studio uses the same editor/rendering components and keeps each test's profile isolated.
test('ordered processes preserve content and persist paper colour, masks and operation order', async ({ page }, info) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({ width: 1500, height: 1000 }); await page.goto('/?card-studio');
  const nav = page.getByRole('navigation', { name: '设计步骤' });
  await expect(nav.locator(':scope > button, .process-layer-select')).toHaveCount(3);
  for (const name of ['卡纸', '选择油墨第 1 层', '成品']) await expect(nav.getByRole('button', { name, exact: true })).toBeVisible();
  await expect(nav.getByRole('button', { name: '卡纸', exact: true })).toHaveAttribute('aria-current', 'step');
  await expect(page.getByRole('button', { name: '纸色 #dbe3ed', exact: true })).toHaveCSS('background-color', 'rgb(219, 227, 237)');
  await expect(page.getByRole('button', { name: '纸色 #233332', exact: true })).toHaveCSS('border-radius', '50%');
  await page.getByRole('button', { name: '棉纤粗纹', exact: true }).click();
  await page.getByRole('button', { name: '纸色 #dbe3ed', exact: true }).click();
  expect((await surface(page)).design!.production!.stock).toEqual({ type: 'cotton', grain: .85, color: '#dbe3ed' });
  await page.getByRole('button', { name: '下一步', exact: true }).click();
  await page.locator('[data-edit-element="title"]').click();
  await page.locator('.face-properties').getByRole('textbox', { name: '标题', exact: true }).fill('Archive / 01');
  await step(page, '工艺层');
  await addProcess(page, '添加覆膜');
  await page.getByLabel('覆膜类型', {exact:true}).selectOption('aurora');
  await page.getByRole('slider', {name:'工艺强度',exact:true}).fill('38');
  await addProcess(page, '添加压印');
  await page.getByRole('slider', {name:'工艺强度',exact:true}).fill('40');
  await addProcess(page, '添加烫金');
  await page.getByRole('slider', {name:'工艺强度',exact:true}).fill('72');
  await openPanel(page, '文本'); await page.getByRole('button', { name: '标题', exact: true }).click();
  await addProcess(page, '添加 UV');
  const processes = (await surface(page)).design!.production!.layers!;
  expect(processes.map(layer=>layer.kind)).toEqual(['ink','laminate','emboss','foil','uv']);
  expect(processes[3].content!.elementIds).toHaveLength(1);
  await step(page, '成品'); await page.getByLabel('对比未加工卡面').check();
  await expect(page.locator('.face-proof-base')).toBeVisible();
  await expect(page.locator('.face-stage-size .factory-artwork')).toHaveAttribute('data-print-proof', 'composite');
  await expect(page.locator('.face-stage-size [data-kind=title]')).toHaveText('Archive / 01');
  await page.screenshot({ path: info.outputPath('production-proof.png'), fullPage: true });
  await page.getByLabel('查看印刷层').selectOption('finishing');
  await expect(page.locator('.face-stage-size .factory-artwork')).toHaveAttribute('data-print-proof', 'finishing');
  await page.getByRole('button', { name: '保存为工艺配方', exact: true }).click();
  await page.getByRole('textbox', { name: '配方名称', exact: true }).fill('Archive gold');
  await page.getByRole('button', { name: '保存配方', exact: true }).click();
  await expect(page.locator('.face-panel-scroll [role=status]')).toHaveText('配方已保存');
  await page.reload(); await page.getByRole('button', { name: '预设', exact: true }).click();
  await page.getByRole('button', { name: 'Archive gold', exact: true }).click();
  const face = await draft(page); expect(face.title).toBe('Field Notes');
  for (const mode of ['node', 'preview', 'inspector', 'workspace'] as const) {
    expect(face.studio!.modes[mode]!.design!.production).toMatchObject({ stock: { type: 'cotton', color: '#dbe3ed' }, layers: processes });
  }
  await step(page, '成品'); await page.getByLabel('查看印刷层').selectOption('composite');
  await expect(page.locator('.face-stage-size [data-kind=title]')).toHaveText('Field Notes');
  expect(errors).toEqual([]);
});

test('releasing a preset and dragging a layer retain all other positions and sizes', async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 1000 }); await page.goto('/?card-studio'); await step(page, '工艺层');
  const initial = await surface(page);
  await page.locator('[data-edit-element="icon"]').click();
  await page.getByRole('button', { name: '转为自由图层', exact: true }).click();
  expect(geometry(await surface(page))).toEqual(geometry(initial));
  expect((await surface(page)).elements.find(item => item.id === 'icon')!.placement).toBe('free');
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  expect((await surface(page)).elements).toEqual(initial.elements);
  await page.getByRole('button', { name: '预设', exact: true }).click();
  await page.getByRole('button', { name: '全部自由编辑', exact: true }).click(); await step(page, '工艺层');
  const released = await surface(page);
  expect(geometry(released)).toEqual(geometry(initial));
  expect(released.elements.every(item => item.placement === 'free')).toBe(true);
  await page.locator('[data-edit-element="title"]').click();
  const target = page.locator('[data-edit-element="title"]');
  const box = (await target.boundingBox())!;
  const stageBox = (await page.locator('.face-edit-stage').boundingBox())!;
  const scale = stageBox.width / released.width;
  await page.keyboard.down('Alt');
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 17 * scale, box.y + box.height / 2 + 9 * scale, { steps: 5 });
  await page.mouse.up(); await page.keyboard.up('Alt');
  const moved = await surface(page);
  const before = released.elements.find(item => item.id === 'title')!, after = moved.elements.find(item => item.id === 'title')!;
  expect(after.x).toBeCloseTo(before.x + 17, 1); expect(after.y).toBeCloseTo(before.y + 9, 1);
  expect([after.width, after.height, after.font_size]).toEqual([before.width, before.height, before.font_size]);
  expect(moved.elements.filter(item => item.id !== 'title')).toEqual(released.elements.filter(item => item.id !== 'title'));
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  expect(geometry(await surface(page))).toEqual(geometry(released));
});

test('content and shape inks compose in editable print order', async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 1000 }); await page.goto('/?card-studio'); await step(page, '工艺层');
  await page.getByRole('button', { name: '预设', exact: true }).click(); await page.getByRole('button', { name: '空白版面', exact: true }).click();
  await page.getByRole('button', {name:'选择油墨第 1 层',exact:true}).click();
  const add = page.locator('.process-elements-panel');
  await openPanel(page, '图形');
  await add.getByRole('button', { name: '矩形', exact: true }).click();
  await page.getByRole('slider', { name: '油墨浓度', exact: true }).fill('45');
  const coloured = (await surface(page)).shapes[1];
  expect(coloured.print).toEqual({ opacity: .45, blend: 'normal' });
  await expect(page.locator('.face-stage-size .factory-shapes > rect').nth(1)).toHaveCSS('opacity', '0.45');
  await expect(page.locator('.face-stage-size .factory-shapes > rect').nth(1)).toHaveCSS('mix-blend-mode', 'normal');
  await add.getByRole('button', { name: '圆形', exact: true }).click();
  const circle = (await surface(page)).shapes[2];
  await page.getByRole('button', { name: '编辑矩形', exact: true }).click(); await page.getByRole('button', { name: '置顶', exact: true }).click();
  expect((await surface(page)).shapes.map(item => item.id)).toEqual([(await surface(page)).shapes[0].id, circle.id, coloured.id]);
  await openPanel(page, '文本');
  await add.getByRole('button', { name: '自由文本', exact: true }).click();
  await page.locator('.face-properties').getByRole('textbox', { name: '自由文本', exact: true }).fill('第二道油墨');
  await page.getByRole('slider', { name: '油墨浓度', exact: true }).fill('65');
  const printed = await surface(page);
  expect(printed.elements[0].print).toEqual({ opacity: .65, blend: 'normal' });
  await expect(page.locator('.face-stage-size [data-kind=text]')).toHaveCSS('opacity', '0.65');
  await expect(page.locator('.face-stage-size [data-kind=text]')).toHaveCSS('mix-blend-mode', 'normal');
  await step(page, '工艺层'); await addProcess(page, '添加覆膜');
  await page.getByLabel('覆膜类型', {exact:true}).selectOption('holo');
  await step(page, '成品');
  expect((await surface(page)).elements).toEqual(printed.elements);
  await expect(page.locator('.face-stage-size [data-kind=text]')).toHaveText('第二道油墨');
});

test('step rail supports keyboard navigation and fits a narrow viewport', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 }); await page.goto('/?card-studio');
  const nav = page.getByRole('navigation', { name: '设计步骤' });
  await page.getByRole('button', { name: '下一步', exact: true }).click();
  await expect(nav.getByRole('button', { name: '选择油墨第 1 层', exact: true })).toHaveAttribute('aria-current', 'step');
  await nav.getByRole('button', { name: '选择油墨第 1 层', exact: true }).focus(); await page.keyboard.press('ArrowLeft');
  await expect(nav.getByRole('button', { name: '卡纸', exact: true })).toHaveAttribute('aria-current', 'step');
  await step(page, '工艺层'); await addProcess(page, '添加覆膜');
  await page.getByLabel('覆膜类型', {exact:true}).selectOption('holo');
  await page.getByRole('button', { name: '下一步', exact: true }).click();
  await expect(page.getByLabel('查看印刷层')).toBeVisible();
  await expect(page.locator('.face-stage-size .factory-artwork')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: info.outputPath('production-mobile.png'), fullPage: true });
});


test('uploaded process artwork survives reorder, duplication and visibility changes', async ({ page }) => {
  await page.goto('/?card-studio'); await addProcess(page, '添加烫金');
  const png = await page.evaluate(() => { const c = document.createElement('canvas'); c.width = 64; c.height = 64; const ctx = c.getContext('2d')!; ctx.fillStyle = 'white'; ctx.beginPath(); ctx.arc(32,32,24,0,Math.PI*2); ctx.fill(); return c.toDataURL(); });
  await openPanel(page, '图像');
  await page.getByLabel('上传图像元素').setInputFiles({ name: 'circle.png', mimeType: 'image/png', buffer: Buffer.from(png.split(',')[1], 'base64') });
  await expect.poll(async () => (await surface(page)).elements.at(-1)!.image_png).toBe(png);
  await page.getByRole('button', { name: '提前烫金第 2 层', exact: true }).click();
  await page.getByRole('button', { name: '复制当前工艺层', exact: true }).click();
  const copied = await surface(page);
  expect(copied.elements.filter(item => item.image_png === png)).toHaveLength(2);
  await page.getByRole('button', { name: '隐藏烫金第 2 层', exact: true }).click();
  expect((await surface(page)).design!.production!.layers![1].enabled).toBe(false);
  await page.getByRole('button', { name: '删除当前工艺层', exact: true }).click();
  expect((await surface(page)).elements.filter(item => item.image_png === png)).toHaveLength(1);
});
