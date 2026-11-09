import { expect, test } from '@playwright/test';

test.beforeEach(async ({ request, page }) => {
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-theme': 'light', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }) },
  } })).ok()).toBe(true);
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Open settings', exact: true })).toBeVisible();
});

test('settings use the shared menu, preserve keyboard focus and persist the chosen language', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.getByRole('button', { name: 'Open settings', exact: true }).click();
  const language = page.getByRole('combobox', { name: 'Language', exact: true });
  await language.click();
  const menu = page.locator('.ui-select-menu');
  await expect(menu).toBeVisible();
  await expect(menu.getByRole('option')).toHaveCount(2);
  await expect(menu.getByRole('option', { name: 'English' })).toHaveAttribute('aria-selected', 'true');
  await expect(language).toBeFocused();
  await page.screenshot({ path: info.outputPath('settings-light.png'), animations: 'disabled' });

  await language.press('Home');
  await language.press('Enter');
  await expect(page.getByRole('dialog', { name: '设置', exact: true })).toBeVisible();
  await expect(menu).toHaveCount(0);
  const chineseLanguage = page.getByRole('combobox', { name: '界面语言', exact: true });
  await expect(chineseLanguage).toHaveValue('zh-CN');
  await chineseLanguage.click();
  await chineseLanguage.press('Escape');
  await expect(page.getByRole('dialog', { name: '设置', exact: true })).toBeVisible();
  await expect(chineseLanguage).toBeFocused();
  await page.reload();
  await page.getByRole('button', { name: '打开设置', exact: true }).click();
  await expect(page.getByLabel('界面语言', { exact: true })).toHaveValue('zh-CN');
  expect(errors).toEqual([]);
});

test('settings menu stays usable in the dark theme', async ({ page }, info) => {
  await page.getByRole('button', { name: 'Open settings', exact: true }).click();
  await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
  await page.getByLabel('Language', { exact: true }).click();
  const menu = page.locator('.ui-select-menu');
  await expect(menu).toBeVisible();
  await page.screenshot({ path: info.outputPath('settings-dark.png'), animations: 'disabled' });
  await page.getByRole('button', { name: 'Close settings', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Settings', exact: true })).toHaveCount(0);
  await expect(menu).toHaveCount(0);
});

test('dynamic plugin fields open above clipped surfaces and keep native form events', async ({ page }) => {
  await page.evaluate(() => {
    const host = document.createElement('div');
    host.style.cssText = 'position:fixed;right:8px;bottom:8px;width:180px;height:36px;overflow:hidden;transform:translateZ(0)';
    host.innerHTML = '<select aria-label="Plugin choice" style="width:180px;height:36px"><option value="one">One</option><option value="blocked" disabled>Blocked</option><option value="two">Two</option></select>';
    const select = host.querySelector('select')!;
    select.addEventListener('change', () => host.dataset.value = select.value);
    document.body.append(host);
  });
  const select = page.getByRole('combobox', { name: 'Plugin choice' });
  await select.click();
  const menu = page.locator('.ui-select-menu');
  await expect(menu).toBeVisible();
  const anchor = (await select.boundingBox())!;
  const box = (await menu.boundingBox())!;
  expect(box.y + box.height).toBeLessThan(anchor.y);
  expect(box.x + box.width).toBeLessThanOrEqual(1440);
  await menu.getByRole('option', { name: 'Blocked' }).click({ force: true });
  await expect(select).toHaveValue('one');
  await expect(menu).toBeVisible();
  await menu.getByRole('option', { name: 'Two' }).click();
  await expect(select).toHaveValue('two');
  expect(await select.evaluate(element => element.parentElement!.dataset.value)).toBe('two');
  await expect(select).toBeFocused();
  await select.click();
  await page.getByRole('button', { name: 'Open settings', exact: true }).click();
  await expect(menu).toHaveCount(0);
});
