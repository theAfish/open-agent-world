import { expect, test, type Locator } from '@playwright/test';
import type { LibrarySnapshot } from '../src/state/cardLibrary';

test('Library filters stay anchored above animated and clipped dialog content', async ({ page, request }, testInfo) => {
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
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  await library.getByRole('button', { name: /^Cards/ }).click();
  const checkMenu = async (select: Locator) => {
    const label = await select.getAttribute('aria-label') ?? '';
    await select.click();
    const menu = page.getByRole('listbox', { name: label });
    await expect(menu).toBeVisible();
    await expect.poll(async () => {
      const field = (await select.boundingBox())!, popup = (await menu.boundingBox())!;
      const viewport = page.viewportSize()!;
      return Math.abs(popup.x - field.x) < 1
        && (Math.abs(popup.y - field.y - field.height - 4) < 1 || Math.abs(popup.y + popup.height - field.y + 4) < 1)
        && popup.x >= 0 && popup.x + popup.width <= viewport.width
        && popup.y >= 0 && popup.y + popup.height <= viewport.height;
    }).toBe(true);
    const option = menu.getByRole('option').last();
    await option.scrollIntoViewIfNeeded();
    // Bounding boxes alone would miss clipping by the Library dialog.
    expect(await option.evaluate(element => {
      const box = element.getBoundingClientRect();
      return element.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
    })).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`filter-${page.viewportSize()!.width}-${label}.png`) });
    await page.keyboard.press('Escape');
    await expect(menu).toHaveCount(0);
    await expect(library).toBeVisible();
  };
  for (const viewport of [{ width: 1920, height: 1080 }, { width: 1280, height: 700 }, { width: 390, height: 800 }]) {
    await page.setViewportSize(viewport);
    await checkMenu(library.getByRole('combobox', { name: 'Source pack', exact: true }));
    await checkMenu(library.getByRole('combobox', { name: 'Card category', exact: true }));
  }
  // Native modal dialogs also need their menus to remain in the top layer.
  await page.evaluate(() => {
    const dialog = document.createElement('dialog');
    dialog.style.cssText = 'padding: 20px; translate: 25px 10px; overflow: hidden';
    dialog.innerHTML = '<select aria-label="Modal choice"><option>First</option><option>Second</option></select>';
    document.body.append(dialog);
    dialog.showModal();
  });
  const modalChoice = page.getByRole('combobox', { name: 'Modal choice', exact: true });
  await checkMenu(modalChoice);
  await modalChoice.click();
  await page.getByRole('listbox', { name: 'Modal choice' }).getByRole('option', { name: 'Second' }).click();
  await expect(modalChoice).toHaveValue('Second');
  await page.evaluate(() => document.querySelector('dialog:modal')!.remove());
});
