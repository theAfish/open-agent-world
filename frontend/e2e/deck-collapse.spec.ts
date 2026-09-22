import { expect, test } from '@playwright/test';

for (const width of [1600, 720]) {
  test(`deck collapses after pointer selection but preserves keyboard and editing focus (${width}px)`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    const tray = page.locator('.component-palette');
    const stage = tray.locator('.deck-stage');
    const legions = tray.getByRole('tab', { name: /Legions/ });
    await legions.click();
    await expect(stage).toBeVisible();
    await page.mouse.move(10, 10);
    await expect(legions).toBeFocused();
    await expect(stage).toBeHidden();

    // Shift+Tab then Tab reaches the same tab using actual keyboard modality.
    await page.keyboard.press('Shift+Tab');
    await page.keyboard.press('Tab');
    await expect(legions).toBeFocused();
    await expect(stage).toBeVisible();
    await page.keyboard.press('Enter');
    await expect(stage).toBeVisible();
    for (let i = 0; i < 30 && await tray.evaluate(element => element.contains(document.activeElement)); i++) {
      await page.keyboard.press('Shift+Tab');
    }
    await expect(stage).toBeHidden();

    await tray.getByRole('tab').first().click();
    await page.mouse.move(10, 10);
    await expect(stage).toBeHidden();
    await tray.getByRole('button', { name: 'Create a new card deck' }).click();
    const input = tray.getByRole('textbox', { name: 'Deck name', exact: true });
    await input.fill('Keep editing');
    await page.mouse.move(10, 10);
    await expect(input).toBeFocused();
    await expect(stage).toBeVisible();
    await tray.getByRole('button', { name: 'Cancel creating deck' }).click();
    await page.mouse.move(10, 10);
    await expect(stage).toBeHidden();
  });
}
