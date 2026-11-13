import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';

// Browser hit testing needs real CSS, but no running application or backend.
test('connection rims yield to content and remain reachable outside cards at every zoom', async ({ page }) => {
  await page.setContent('<main></main>');
  for (const file of ['node_modules/@xyflow/react/dist/style.css', 'src/theme.css',
    'src/cards/contentSurface.css', 'src/cards/cardLOD.css']) {
    await page.addStyleTag({ content: readFileSync(file, 'utf8') });
  }
  for (const zoom of [0.5, 1, 2]) {
    for (const shape of ['rectangle', 'ellipse']) {
      for (const lod of [false, true]) {
        await page.locator('main').evaluate((root, { zoom, shape, lod }) => {
          root.innerHTML = `<article class="world-card ${lod ? 'card-lod-view' : ''}"
            data-card-shape="${shape}" style="position:absolute;left:200px;top:150px;
            width:200px;height:200px;min-height:0;padding:0;border:0;
            transform-origin:top left;transform:scale(${zoom});--canvas-ui-scale:${1 / zoom};
            border-radius:${shape === 'ellipse' ? '50%' : '12px'}">
            ${['top', 'right', 'bottom', 'left'].map(side => `<div class="react-flow__handle semantic-handle semantic-handle--${side} connectable connectionindicator" data-side="${side}"></div>`).join('')}
            <button id="control" style="position:absolute;left:0;top:80px;width:24px;height:40px;padding:0">Action</button>
            <div id="grip" class="node-drag-region" style="position:absolute;right:0;top:80px;width:24px;height:40px;overflow:hidden">Move</div>
          </article>`;
        }, { zoom, shape, lod });
        const hit = (x: number, y: number) => page.evaluate(({ x, y }) => {
          const target = document.elementFromPoint(x, y) as HTMLElement | null;
          return target?.id || target?.dataset.side || null;
        }, { x, y });
        const midY = 150 + 100 * zoom;
        expect(await hit(201, midY), `${zoom}/${shape}/${lod}: button`).toBe('control');
        expect(await hit(200 + 200 * zoom - 1, midY), 'move grip').toBe('grip');
        expect(await hit(194, midY), `${zoom}/${shape}/${lod}: outer left rim`).toBe('left');
        expect(await hit(200 + 200 * zoom + 6, midY), 'outer right rim').toBe('right');
        expect(await hit(200 + 100 * zoom, 144), 'outer top rim').toBe('top');
        expect(await hit(200 + 100 * zoom, 150 + 200 * zoom + 6), 'outer bottom rim').toBe('bottom');
        expect(await hit(200 + 100 * zoom, midY), 'body is not a connection source').toBeNull();
      }
    }
  }
});
