import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const styles = [
  '../plugins/knowledge_base/frontend/style.css',
  '../plugins/knowledge_base/frontend/ProjectWorkspace.css',
  'src/graph/networkMap.css',
].map(file => readFileSync(path.resolve(process.cwd(), file), 'utf8')).join('\n');

test('knowledge graph fills its reserved height in results and insights', async ({ page }) => {
  // Exercise the real styles in both host layouts without a backend or WebGL.
  await page.setContent(`<style>${styles}</style>
    <section class="knowledge-section project-workspace">
      <div class="project-result-detail">
        <div class="knowledge-map"><div class="network-map"><div class="network-map-stage"></div></div></div>
      </div>
      <div class="knowledge-graph">
        <div class="knowledge-map"><div class="network-map"><div class="network-map-stage"></div></div></div>
        <aside class="knowledge-graphside">Entity details</aside>
      </div>
    </section>`);
  for (const width of [1200, 500, 1200]) {
    await page.setViewportSize({ width, height: 900 });
    for (const host of ['.project-result-detail', '.knowledge-graph']) {
      const wrapper = page.locator(`${host} .knowledge-map`);
      const stage = wrapper.locator('.network-map-stage');
      const reserved = await wrapper.boundingBox();
      const rendered = await stage.boundingBox();
      expect(reserved!.height).toBeGreaterThanOrEqual(width === 500 ? 360 : 440);
      expect(rendered!.height).toBeCloseTo(reserved!.height, 0);
      expect(rendered!.width).toBeCloseTo(reserved!.width, 0);
    }
  }
});
