import { expect, type Locator, type Page } from '@playwright/test';

export async function mapReady(scope: Locator) {
  await expect(scope.locator('.network-map')).toHaveAttribute('data-layout-state', 'ready', { timeout: 30000 });
  await expect(scope.locator('canvas.sigma-nodes')).toBeVisible();
}
export async function mapPoint(scope: Locator, id?: string) {
  const stage = scope.locator('.network-map-stage');
  return stage.evaluate((element, id) => {
    const el = element as HTMLElement & { networkDiagnostics: { positions: { id: string; screenX: number; screenY: number }[] } };
    const node = id ? el.networkDiagnostics.positions.find(n => n.id === id) : el.networkDiagnostics.positions[0];
    if (!node) throw new Error(`Missing map node ${id}`);
    const rect = el.getBoundingClientRect();
    return { x: rect.x + node.screenX * rect.width / el.clientWidth, y: rect.y + node.screenY * rect.height / el.clientHeight };
  }, id);
}
export async function clickMapNode(page: Page, scope: Locator, id?: string) {
  await mapReady(scope); await page.waitForTimeout(320);
  const p = await mapPoint(scope, id); await page.mouse.click(p.x, p.y);
}
export async function mapIds(scope: Locator) {
  return scope.locator('.network-map-stage').evaluate(el => (el as any).networkDiagnostics.positions.map((n: {id: string}) => n.id) as string[]);
}
