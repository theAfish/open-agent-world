import { expect, test } from '@playwright/test';
import { graphFixture } from './fixtures/executionGraph';
import { writeFileSync, mkdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

// Sample the browser's actual SVG arcs, including S bends.
function vertexCollisions(element: HTMLElement | SVGElement) {
  const vertices = [...element.querySelectorAll<HTMLElement>('.execution-vertex')].map(row => ({ id: row.dataset.rowId!,
    x: parseFloat((row.querySelector('.execution-vertex-dot') as HTMLElement).style.left), y: parseFloat(row.style.top) + 27 }));
  const errors: string[] = [];
  for (const path of element.querySelectorAll<SVGPathElement>('path.execution-track')) {
    const length = path.getTotalLength();
    for (let distance = 0; distance <= length; distance += 3) {
      const point = path.getPointAtLength(distance);
      for (const vertex of vertices) if (vertex.id !== path.dataset.source && vertex.id !== path.dataset.target
        && Math.hypot(point.x - vertex.x, point.y - vertex.y) < 8) errors.push(`${path.dataset.source}→${path.dataset.target} crosses ${vertex.id}`);
    }
  }
  return [...new Set(errors)];
}

for (const scenario of ['independent', 'sparse', 'parallel', 'large', 'braid', 'compact']) {
  test(scenario + ': 300, 500 and 1000px in both themes', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    for (const theme of ['light', 'dark']) for (const width of [300, 500, 1000]) {
      await page.goto('/dev/execution-graph.html?scenario=' + scenario + '&theme=' + theme + '&width=' + width);
      const graph = page.getByRole('region', { name: 'Execution graph', exact: true });
      const viewport = graph.getByLabel('Execution history', { exact: true });
      await expect(graph.locator('.execution-vertex')).not.toHaveCount(0);
      if (scenario === 'large' || scenario === 'sparse') await viewport.evaluate(element => { element.scrollTop = element.scrollHeight; });
      await expect.poll(() => viewport.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
      await expect(graph.locator('.execution-tracks')).toHaveCount(1);
      await expect(graph.locator('.execution-expand, .execution-fold, .execution-selection-actions, .execution-lens-viewport')).toHaveCount(0);
      if (scenario === 'independent') {
        await expect(graph.locator('[data-graph-edge]')).toHaveCount(0);
        await expect(graph.locator('path[data-kind="history"]')).toHaveCount(0);
      }
      if (scenario === 'sparse') {
        await expect(graph.locator('[data-graph-edge]')).toHaveCount(0);
        await expect(graph.locator('[data-row-id="E20"] .execution-reference-count')).toHaveText('↗3');
      }
      if (scenario === 'parallel') expect(await graph.locator('[data-graph-edge]').count()).toBeGreaterThanOrEqual(6);
      if (scenario === 'compact') {
        expect(await graph.evaluate(vertexCollisions)).toEqual([]);
        for (const [source, target] of [['validate', 'accept-deep'], ['accept-deep', 'remote']]) {
          const path = graph.locator(`path[data-source="${source}"][data-target="${target}"]`);
          expect((await path.getAttribute('d'))?.match(/A/g)?.length).toBe(3); // S bend plus endpoint arc
        }
      }
      const metrics = await graph.evaluate(element => ({ vertices: element.querySelectorAll('.execution-vertex').length,
        lanes: Array.from(element.querySelectorAll('.execution-vertex-dot')).map(dot => Number(dot.getAttribute('data-lane'))) }));
      expect(metrics.vertices).toBeLessThan(25);
      expect(Math.max(...metrics.lanes)).toBeLessThan(4);
      await page.mouse.move(0, 0);
      await graph.screenshot({ path: '../.outputs/project-history/' + scenario + '-' + theme + '-' + width + '.png', animations: 'disabled' });
    }
    expect(errors).toEqual([]);
  });
}

test('one-click details, distant dependency navigation, search and stable live history', async ({ page }) => {
  await page.goto('/dev/execution-graph.html?scenario=sparse&width=500');
  const graph = page.getByRole('region', { name: 'Execution graph', exact: true });
  const viewport = graph.getByLabel('Execution history', { exact: true });
  await expect(graph.getByText('20 events')).toBeVisible();
  await viewport.evaluate(element => { element.scrollTop = element.scrollHeight; });
  const last = graph.getByRole('button', { name: 'E20: running', exact: true });
  await last.locator('.execution-vertex-dot').click();
  await expect(last).toHaveAttribute('aria-expanded', 'true');
  await expect(graph.locator('.execution-anchors button')).toHaveCount(3);
  await expect(graph.locator('path[data-reference="true"]')).toHaveCount(0);
  await graph.screenshot({ path: '../.outputs/project-history/sparse-selected-500.png', animations: 'disabled' });
  await last.locator('strong').click();
  await expect(graph.getByLabel('Event details')).toHaveCount(0);
  await last.click();
  await graph.getByRole('button', { name: 'Uses result E01', exact: true }).click();
  await expect(graph.getByRole('button', { name: 'E01: success', exact: true })).toHaveAttribute('aria-expanded', 'true');
  await expect(graph.getByText('20 events')).toBeVisible(); // navigation does not hide the rest
  await graph.getByRole('button', { name: 'Used by E20', exact: true }).click();
  await expect(last).toHaveAttribute('aria-expanded', 'true');
  await graph.getByRole('textbox', { name: 'Find event' }).fill('E09');
  await page.keyboard.press('Enter');
  await expect(graph.getByRole('button', { name: 'E09: success' })).toHaveAttribute('aria-expanded', 'true');
  const before = await viewport.evaluate(element => ({ top: element.scrollTop,
    lanes: Array.from(element.querySelectorAll<HTMLElement>('.execution-vertex')).map(row => [row.dataset.rowId, row.querySelector('.execution-vertex-dot')?.getAttribute('data-lane')]) }));
  const value = graphFixture('sparse'); value.revision = 2;
  value.nodes.at(-1)!.status = 'failed';
  value.nodes.push({ id: 'retry', label: 'Retry E20', kind: 'agent', status: 'running', sequence: 210, retry_of: 'E20' });
  value.edges!.push({ id: 'retry-edge', source: 'E20', target: 'retry', kind: 'retry' });
  await page.evaluate(value => (window as unknown as { updateGraphFixture(value: unknown): void }).updateGraphFixture(value), value);
  await expect(graph.getByText('21 events')).toBeVisible();
  await expect(graph.getByRole('button', { name: 'E09: success' })).toHaveAttribute('aria-expanded', 'true');
  expect(await viewport.evaluate(element => element.scrollTop)).toBeCloseTo(before.top, 0);
  const after = await viewport.locator('.execution-vertex').evaluateAll(rows => rows.map(row => [row.getAttribute('data-row-id'), row.querySelector('.execution-vertex-dot')?.getAttribute('data-lane')]));
  // The new-activity button reduces viewport height and may unmount a bottom
  // overscan row. Retained events must keep their lanes and the scroll anchor.
  const previousLanes = new Map(before.lanes.map(([id, lane]) => [id, lane]));
  for (const [id, lane] of after) if (previousLanes.has(id!)) expect(lane).toBe(previousLanes.get(id!));
  await graph.getByRole('button', { name: '1 new events' }).click();
  await expect(graph.getByRole('button', { name: 'E20: failed' })).toBeVisible();
  await expect(graph.getByRole('button', { name: 'Retry E20: running' })).toHaveAttribute('aria-expanded', 'true');
  await graph.screenshot({ path: '../.outputs/project-history/retry-preserved-500.png', animations: 'disabled' });
  await viewport.focus(); await page.keyboard.press('Escape');
  await expect(graph.getByLabel('Event details')).toHaveCount(0);
});

test('expanded details keep readable timing and relation lists without horizontal overflow', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  for (const theme of ['light', 'dark']) for (const width of [300, 500, 1000]) for (const locale of ['en', 'zh-CN']) {
    await page.goto(`/dev/execution-graph.html?scenario=details&theme=${theme}&width=${width}&locale=${locale}`);
    const graph = page.locator('.execution-graph');
    await graph.getByRole('button', { name: 'Before deleting, archive the only two delete targets: success', exact: true }).click();
    const detail = graph.locator('.execution-inline');
    await expect(detail.locator('.execution-duration')).toHaveText(locale === 'en' ? '22s' : '22 秒');
    await expect(detail.locator('.execution-time-range time')).toHaveCount(2);
    await expect(detail.locator('.execution-relation-group')).toHaveCount(2);
    await expect(detail.locator('.execution-anchors li')).toHaveCount(4);
    await expect(detail.getByRole('button', { name: /outside-retention/ })).toBeDisabled();
    await expect.poll(() => detail.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    expect(await graph.locator('.execution-graph-viewport').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.mouse.move(0, 0);
    await graph.screenshot({ path: `../.outputs/project-history/details-${theme}-${width}-${locale}.png`, animations: 'disabled' });
    await detail.locator('.execution-artifacts').scrollIntoViewIfNeeded();
    expect(await detail.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await detail.getByRole('button', { name: /Write \/workspace\/cleanup_manifest.json/ }).click();
    await expect(graph.locator('[data-row-id="manifest"]')).toHaveAttribute('data-selected', 'true');
  }
  expect(errors).toEqual([]);
});

test('long details use one scrollbar, reserve its width and return to the event on collapse', async ({ page }) => {
  for (const theme of ['light', 'dark']) for (const width of [300, 386, 500, 706, 1000]) {
    await page.goto(`/dev/execution-graph.html?scenario=details&theme=${theme}&width=${width}`);
    const graph = page.locator('.execution-graph');
    const viewport = graph.locator('.execution-graph-viewport');
    const selectedRow = graph.locator('[data-row-id="preserve"]');
    const title = selectedRow.locator('.execution-vertex-label');
    await expect(title).toBeVisible();
    const geometry = () => viewport.evaluate(element => {
      const title = element.querySelector('[data-row-id="preserve"] .execution-vertex-label')!.getBoundingClientRect();
      return { width: element.clientWidth, titleX: title.x, titleWidth: title.width };
    });
    const before = await geometry();
    expect(await viewport.evaluate(el => el.scrollHeight <= el.clientHeight)).toBe(true);
    await selectedRow.locator('.execution-node-select').click();
    const detail = graph.locator('.execution-inline');
    await expect.poll(() => viewport.evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true);
    expect(await geometry()).toEqual(before);
    const scrollOwners = () => graph.evaluate(element => [...element.querySelectorAll<HTMLElement>('*')]
      .filter(el => ['auto', 'scroll'].includes(getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight)
      .map(el => el.className));
    expect(await scrollOwners()).toEqual(['execution-graph-viewport']);
    await expect.poll(() => detail.evaluate(el => el.scrollHeight <= el.clientHeight + 1)).toBe(true);
    const scrollBeforeWheel = await viewport.evaluate(el => el.scrollTop);
    await detail.locator('.execution-detail-tags').hover();
    await page.mouse.wheel(0, 160);
    await expect.poll(() => viewport.evaluate(el => el.scrollTop)).toBeGreaterThan(scrollBeforeWheel);
    await detail.locator('.execution-detail-identity').scrollIntoViewIfNeeded();
    await expect(detail.locator('.execution-detail-identity')).toBeInViewport();
    await expect(detail).toHaveCount(1); // Virtualization retains the entire expanded event.
    expect(await viewport.evaluate(el => el.scrollTop)).toBeGreaterThan(100);
    expect(await detail.evaluate(el => el.scrollTop)).toBe(0);
    expect(await scrollOwners()).toEqual(['execution-graph-viewport']);
    expect(await detail.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await viewport.focus();
    await page.keyboard.press('Escape');
    await expect(detail).toHaveCount(0);
    await expect(selectedRow.locator('.execution-node-select')).toBeInViewport();
    expect(await geometry()).toEqual(before);
    expect(await viewport.evaluate(el => el.scrollHeight <= el.clientHeight)).toBe(true);
  }
});

test('collapsing a tall detail retains its event when later history still fills the viewport', async ({ page }) => {
  await page.goto('/dev/execution-graph.html?scenario=details&width=500');
  const value = graphFixture('details');
  value.revision = 2;
  value.nodes[1].detail = Array.from({ length: 50 }, (_, i) => `Archived output ${i + 1}; its original contents remain available.`).join('\n');
  value.nodes.push(...Array.from({ length: 60 }, (_, i) => ({ id: 'later-' + i, label: 'Later event ' + i, kind: 'agent' as const, status: 'success', sequence: i + 5 })));
  await page.evaluate(value => (window as unknown as { updateGraphFixture(value: unknown): void }).updateGraphFixture(value), value);
  const viewport = page.locator('.execution-graph-viewport');
  await page.locator('[data-row-id="preserve"] .execution-node-select').click();
  await page.locator('.execution-detail-identity').scrollIntoViewIfNeeded();
  await expect.poll(() => viewport.evaluate(el => el.scrollTop)).toBeGreaterThan(1000);
  await viewport.focus();
  await page.keyboard.press('Escape');
  await expect(page.locator('.execution-inline')).toHaveCount(0);
  await expect(page.locator('[data-row-id="preserve"] .execution-node-select')).toBeInViewport();
  expect(await viewport.evaluate(el => el.scrollTop)).toBeLessThan(200);
  expect(await viewport.evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true);
});

test('braid keeps identity separate, traces causes, navigates across executions and respects reduced motion', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/dev/execution-graph.html?scenario=braid&width=500');
  const graph = page.getByRole('region', { name: 'Execution graph', exact: true });
  const viewport = graph.getByLabel('Execution history', { exact: true });
  const row = (id: string) => graph.locator(`[data-row-id="${id}"]`);
  const lane = (id: string) => row(id).locator('.execution-vertex-dot').getAttribute('data-lane');
  const revealSelection = () => viewport.evaluate(element => {
    const selected = element.querySelector<HTMLElement>('[data-selected="true"]')!;
    element.scrollTop = Math.max(0, parseFloat(selected.style.top) - 220);
  });
  await expect(graph.getByText('20 events')).toBeVisible();
  expect(await lane('scan')).toBe(await lane('handoff'));
  expect(await lane('failed')).toBe(await lane('retry'));
  expect(await lane('build')).not.toBe(await lane('baseline'));
  expect(await row('build').locator('.execution-vertex-dot').getAttribute('class')).toBe(await row('baseline').locator('.execution-vertex-dot').getAttribute('class'));
  await row('combine').locator('.execution-node-select').click();
  // Full-height details can put the next event outside the virtualized window.
  await viewport.evaluate(element => {
    const selected = element.querySelector<HTMLElement>('[data-selected="true"]')!;
    element.scrollTop = parseFloat(selected.style.top) + selected.offsetHeight - element.clientHeight / 2;
  });
  await expect(row('independent')).toHaveAttribute('data-muted', 'true');
  await row('independent').locator('.execution-node-select').hover();
  await expect(row('combine')).toHaveAttribute('data-selected', 'true');
  await revealSelection();
  await expect(graph.locator('.execution-causal-summary')).toHaveText('7 upstream · 3 downstream');
  await expect(graph.locator('path[data-source="retry"][data-target="combine"]')).toHaveCount(1);
  await expect(graph.locator('path[data-source="retry"][data-target="combine"]')).toHaveAttribute('data-kinds', 'control data');
  await expect(graph.getByText('explicit_input_binding · oaw:artifact:structures:v2')).toBeVisible();
  expect(await graph.evaluate(vertexCollisions)).toEqual([]);
  await page.mouse.move(0, 0);
  await graph.screenshot({ path: '../.outputs/project-history/braid-causes-500.png', animations: 'disabled' });
  await viewport.focus(); await page.keyboard.press('End');
  await expect(row('report')).toHaveAttribute('data-selected', 'true');
  await revealSelection();
  await expect(graph.getByRole('button', { name: 'Uses result outside-retention' })).toBeDisabled();
  await expect(graph.getByText('Execution: execution-3')).toBeVisible();
  await expect(row('review')).toHaveAttribute('data-state', 'custom');
  await graph.getByRole('button', { name: 'Uses result Retry with stable parameters', exact: true }).click();
  await expect(row('retry')).toHaveAttribute('data-selected', 'true');
  await expect(row('failed')).toHaveAttribute('data-status', 'failed');
  await viewport.focus(); await page.keyboard.press('Home');
  await expect(row('plan')).toHaveAttribute('data-selected', 'true');
  await page.keyboard.press('Escape');
  await expect(graph.getByLabel('Event details')).toHaveCount(0);
  await graph.getByRole('textbox', { name: 'Find event' }).fill('Compute transport');
  await page.keyboard.press('Enter');
  expect(await row('transport').locator('.execution-status-ring').first().evaluate(element => getComputedStyle(element).animationName)).toBe('none');
});

test('hover preserves cables and folded dependencies stay navigable without left-side detours', async ({ page }) => {
  for (const theme of ['light', 'dark']) for (const width of [300, 500]) {
    await page.goto(`/dev/execution-graph.html?scenario=braid&width=${width}&theme=${theme}`);
    const graph = page.getByRole('region', { name: 'Execution graph', exact: true });
    const viewport = graph.getByLabel('Execution history', { exact: true });
    const row = (id: string) => graph.locator(`[data-row-id="${id}"]`);
    await expect(graph.getByText('20 events')).toBeVisible();
    const signature = () => graph.locator('path.execution-track').evaluateAll(paths => paths.map(path => ({
      source: path.getAttribute('data-source'), target: path.getAttribute('data-target'), d: path.getAttribute('d'),
    })));
    const before = await signature();
    for (const id of ['plan', 'failed', 'retry']) {
      await row(id).locator('.execution-node-select').hover();
      expect(await signature()).toEqual(before);
      await expect(graph.locator('path[data-reference="true"]')).toHaveCount(0);
      expect(await graph.locator('path[data-highlighted="true"]').count()).toBeGreaterThan(0);
    }
    await row('plan').locator('.execution-node-select').hover();
    await graph.screenshot({ path: `../.outputs/project-history/stable-focus-${theme}-${width}.png`, animations: 'disabled' });
    await row('failed').locator('.execution-node-select').click();
    await expect(row('failed').locator('.execution-reference-count')).not.toBeEmpty();
    await expect(graph.locator('path[data-reference="true"]')).toHaveCount(0);
    await graph.getByRole('button', { name: 'Replanned as Plan transport calculations', exact: true }).click();
    await expect(row('replan')).toHaveAttribute('data-selected', 'true');
    await viewport.focus(); await page.keyboard.press('End');
    await expect(row('report')).toHaveAttribute('data-selected', 'true');
    await expect(graph.getByRole('button', { name: 'Uses result outside-retention' })).toBeDisabled();
    await graph.getByRole('button', { name: 'Uses result Retry with stable parameters', exact: true }).click();
    await expect(row('retry')).toHaveAttribute('data-selected', 'true');
    await expect(graph.locator('path[data-reference="true"]')).toHaveCount(0);
    const minX = await graph.locator('path.execution-track').evaluateAll(paths => Math.min(...paths.flatMap(element => {
      const path = element as SVGPathElement;
      return Array.from({ length: Math.ceil(path.getTotalLength() / 3) + 1 }, (_, i) => path.getPointAtLength(i * 3).x);
    })));
    expect(minX).toBeGreaterThanOrEqual(24); // All curves remain inside the normal routing area.
    expect(await graph.evaluate(vertexCollisions)).toEqual([]);
  }
});

test('contract-size history keeps browser mounting and interaction bounded', async ({ page }) => {
  await page.goto('/dev/execution-graph.html?scenario=empty&width=300');
  const value = graphFixture('empty'); value.history_id = 'contract-limit'; value.title = '10,000 events · 49,985 relations';
  value.nodes = Array.from({ length: 10_000 }, (_, i) => ({ id: 'n' + i, label: 'Task ' + i, kind: 'agent',
    status: i === 9999 ? 'running' : 'success', sequence: i, actor_id: 'agent-' + i % 8, actor_label: 'Agent ' + i % 8 }));
  value.edges = value.nodes.flatMap((node, i) => Array.from({ length: Math.min(5, i) }, (_, j) => ({
    id: `${i}:${j}`, source: 'n' + (i - j - 1), target: node.id, kind: j ? 'data' as const : 'control' as const,
  })));
  await page.evaluate(value => (window as unknown as { updateGraphFixture(value: unknown): void }).updateGraphFixture(value), value);
  const graph = page.getByRole('region', { name: 'Execution graph', exact: true });
  await expect(graph.getByText('10000 events')).toBeVisible();
  const viewport = graph.getByLabel('Execution history', { exact: true });
  const metrics = await viewport.evaluate(async element => {
    const times: number[] = [];
    let maxRows = 0, maxPaths = 0;
    for (let i = 0; i < 20; i++) {
      const start = performance.now(); element.scrollTop = i / 19 * element.scrollHeight;
      await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
      times.push(performance.now() - start);
      maxRows = Math.max(maxRows, element.querySelectorAll('.execution-vertex').length);
      maxPaths = Math.max(maxPaths, element.querySelectorAll('.execution-track').length);
    }
    return { times, maxRows, maxPaths, horizontalOverflow: element.scrollWidth > element.clientWidth };
  });
  expect(metrics.maxRows).toBeLessThan(25); expect(metrics.maxPaths).toBeLessThan(200);
  expect(metrics.horizontalOverflow).toBe(false);
  // Two requestAnimationFrames include ~33 ms at 60 Hz. This is a conservative
  // interaction regression bound, not a claim about single-frame render time.
  const p95 = [...metrics.times].sort((a, b) => a - b)[Math.floor(metrics.times.length * .95)];
  expect(p95).toBeLessThan(250);
  await viewport.focus(); await page.keyboard.press('End');
  await expect(graph.locator('[data-row-id="n9999"]')).toHaveAttribute('data-selected', 'true');
  await expect(graph.locator('.execution-causal-summary')).toHaveText('9999 upstream · 0 downstream');
  mkdirSync('../.outputs/project-history', { recursive: true });
  writeFileSync('../.outputs/project-history/contract-limit-metrics.json', JSON.stringify({ ...metrics, p95 }, null, 2));
});

test('1200 events are readable immediately with windowed scrolling and Chinese empty state', async ({ page }) => {
  await page.goto('/dev/execution-graph.html?scenario=large&width=300');
  const graph = page.getByRole('region', { name: 'Execution graph', exact: true });
  await expect(graph.getByText('1200 events')).toBeVisible();
  const viewport = graph.getByLabel('Execution history', { exact: true });
  const samples = await viewport.evaluate(async element => {
    const times: number[] = [];
    for (let i = 0; i < 30; i++) {
      const start = performance.now(); element.scrollTop = i / 29 * element.scrollHeight;
      await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
      times.push(performance.now() - start);
    }
    return { times, rows: element.querySelectorAll('.execution-vertex').length, height: element.scrollHeight };
  });
  expect(samples.height).toBeGreaterThan(1200 * 54);
  expect(samples.rows).toBeLessThan(25);
  await expect(graph.getByRole('button', { name: 'Publish result: waiting', exact: true })).toBeVisible();
  mkdirSync('../.outputs/project-history', { recursive: true });
  writeFileSync('../.outputs/project-history/scroll-metrics.json', JSON.stringify(samples, null, 2));
  await graph.screenshot({ path: '../.outputs/project-history/large-end-300.png', animations: 'disabled' });
  await page.goto('/dev/execution-graph.html?scenario=empty&width=300&locale=zh-CN&theme=dark');
  await expect(page.locator('.execution-vertex')).toHaveCount(0);
  await page.locator('main').screenshot({ path: '../.outputs/project-history/empty-dark-300.png' });
  await page.goto('/dev/execution-graph.html?scenario=parallel&width=300&locale=zh-CN&theme=dark');
  await expect(page.getByText('项目历史', { exact: true })).toBeVisible();
  await page.locator('main').screenshot({ path: '../.outputs/project-history/parallel-zh-dark-300.png' });
});

test('native worker field filtering still produces readable events and task forks', async ({ page }) => {
  const python = path.resolve('../backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const value = JSON.parse(execFileSync(python, ['e2e/fixtures/native_history.py'], { encoding: 'utf8' }));
  for (const width of [300, 500, 1000]) for (const theme of ['light', 'dark']) {
    await page.goto('/dev/execution-graph.html?scenario=empty&width=' + width + '&theme=' + theme);
    await page.evaluate(value => (window as unknown as { updateGraphFixture(value: unknown): void }).updateGraphFixture(value), value);
    const graph = page.getByRole('region', { name: 'Execution graph', exact: true });
    await expect(graph.getByRole('button', { name: 'Prepare environment: success' })).toBeVisible();
    await expect(graph.getByText('8 events')).toBeVisible();
    await expect(graph.getByRole('button', { name: 'Check runtime: completed' })).toHaveCount(0);
    await expect(graph.getByRole('button', { name: 'Orchestrator: success' })).toHaveCount(0);
    await expect(graph.getByRole('button', { name: 'Execution 1: success' })).toHaveCount(0);
    await expect(graph.locator('[data-graph-edge][data-kind="fork"]')).toHaveCount(1);
    await expect(graph.locator('[data-graph-edge][data-kind="merge"]')).toHaveCount(0);
    await page.mouse.move(0, 0);
    await graph.screenshot({ path: '../.outputs/project-history/native-' + theme + '-' + width + '.png', animations: 'disabled' });
    await graph.getByRole('button', { name: 'Prepare environment: success' }).click();
    await expect(graph.locator('.execution-task-tools')).toContainText('Check runtime');
    await graph.screenshot({ path: '../.outputs/project-history/native-details-' + theme + '-' + width + '.png', animations: 'disabled' });
  }
});

test('a task stays one event through many tool calls, completion and retry', async ({ page }) => {
  for (const width of [300, 500, 1000]) for (const theme of ['light', 'dark']) {
    await page.goto('/dev/execution-graph.html?scenario=empty&width=' + width + '&theme=' + theme);
    const value = graphFixture('parallel');
    value.nodes = value.nodes.slice(0, 3);
    value.nodes[1].status = 'running'; value.nodes[1].result = '';
    value.edges = value.edges!.slice(0, 2);
    const update = async () => page.evaluate(value => (window as unknown as { updateGraphFixture(value: unknown): void }).updateGraphFixture(value), value);
    await update();
    const graph = page.getByRole('region', { name: 'Execution graph', exact: true });
    const task = graph.locator('[data-row-id="E2"]');
    await expect(graph.getByText('3 events')).toBeVisible();
    const lane = await task.locator('.execution-vertex-dot').getAttribute('data-lane');
    for (let i = 0; i < 60; i++) value.nodes.push({ id: 'tool' + i, label: ['Read file', 'Edit file', 'Run tests'][i % 3],
      kind: 'tool', parent_id: 'E2', status: i === 59 ? 'failed' : 'completed', sequence: 10 + i,
      result: i === 59 ? 'Empty input rejected by validation.' : '' });
    value.revision = 2; await update();
    await expect(graph.getByText('3 events')).toBeVisible();
    await expect(graph.locator('.execution-new-activity')).toHaveCount(0);
    await task.locator('button').click();
    await expect(graph.locator('.execution-task-tools')).toContainText('Tool activity · 60');
    await expect(graph.locator('.execution-task-tools li')).toHaveCount(4);
    await expect(graph.locator('.execution-task-tools')).toContainText('Empty input rejected by validation.');
    await expect(task).toHaveAttribute('data-pending', 'true');
    await graph.screenshot({ path: '../.outputs/project-history/event-running-' + theme + '-' + width + '.png', animations: 'disabled' });
    value.nodes[1].status = 'failed'; value.nodes[1].result = 'Parser updated; validation found an empty-input error.';
    value.revision = 3; await update();
    await expect(task.locator('button').first()).toHaveAttribute('aria-expanded', 'true');
    await expect(task).toHaveAttribute('data-pending', 'false');
    await expect(task.locator('.execution-vertex-dot')).toHaveAttribute('data-lane', lane!);
    await expect(graph.locator('.execution-new-activity')).toHaveCount(0);
    await expect(graph.getByText(value.nodes[1].result)).toBeVisible();
    await graph.screenshot({ path: '../.outputs/project-history/event-finished-' + theme + '-' + width + '.png', animations: 'disabled' });
    value.nodes.push({ id: 'retry', label: 'Fix empty input', kind: 'agent', status: 'running', retry_of: 'E2', workstream_id: 'repair', actor_id: 'Atlas', actor_label: 'Atlas' });
    value.edges!.push({ id: 'retry', source: 'E2', target: 'retry', kind: 'retry' });
    value.revision = 4; await update();
    await expect(graph.getByText('4 events')).toBeVisible();
    await expect(graph.getByRole('button', { name: '1 new events' })).toBeVisible();
    await task.locator('.execution-node-select').click();
    await graph.getByRole('textbox', { name: 'Find event' }).fill('Run tests');
    await page.keyboard.press('Enter');
    await expect(task.locator('.execution-node-select')).toHaveAttribute('aria-expanded', 'true');
    await expect.poll(() => graph.locator('.execution-graph-viewport').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  }
});

test('1200 task and tool records stay 120 events across paging and history navigation', async ({ page }) => {
  const value = graphFixture('empty');
  value.title = '120 task results · 1,080 internal tool calls'; value.history_id = 'task-events';
  for (let i = 1; i <= 120; i++) {
    const id = 'E' + String(i).padStart(3, '0');
    value.nodes.push({ id, label: id + ' Validate structure', kind: 'agent', status: 'success',
      actor_id: 'agent-' + i % 3, actor_label: ['Atlas', 'Nova', 'Mira'][i % 3], workstream_id: 'task-' + i,
      result: 'Structure validated; result saved.', sequence: i * 10 });
    for (let j = 0; j < 9; j++) value.nodes.push({ id: id + ':tool' + j, label: ['Read inputs', 'Run calculation', 'Check outputs'][j % 3],
      kind: 'tool', status: 'completed', parent_id: id, sequence: i * 10 + j + 1 });
  }
  value.edges = [1, 9, 17].map(i => ({ id: 'result-' + i, source: 'E' + String(i).padStart(3, '0') + ':tool8', target: 'E120:tool0', kind: 'data' }));
  for (const width of [300, 500, 1000]) for (const theme of ['light', 'dark']) {
    await page.goto('/dev/execution-graph.html?scenario=empty&width=' + width + '&theme=' + theme);
    await page.evaluate(value => (window as unknown as { updateGraphFixture(value: unknown): void }).updateGraphFixture(value), value);
    const graph = page.getByRole('region', { name: 'Execution graph', exact: true });
    await expect(graph.getByText('120 events')).toBeVisible();
    const viewport = graph.getByLabel('Execution history', { exact: true });
    const height = await viewport.evaluate(element => { element.scrollTop = element.scrollHeight; return element.scrollHeight; });
    expect(height).toBeLessThan(120 * 54 + 100);
    await graph.getByRole('button', { name: 'E120 Validate structure: success' }).click();
    await expect(graph.locator('.execution-anchors button')).toHaveCount(3);
    await expect(graph.locator('[data-graph-edge][data-highlighted="true"]')).toHaveCount(0);
    await expect(graph.locator('.execution-task-tools')).toContainText('Tool activity · 9');
    await expect(graph.locator('.execution-vertex')).not.toHaveCount(0);
    expect(await graph.locator('.execution-vertex').count()).toBeLessThan(25);
    await expect.poll(() => viewport.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.mouse.move(0, 0);
    await graph.screenshot({ path: '../.outputs/project-history/task-history-' + theme + '-' + width + '.png', animations: 'disabled' });
    await graph.getByRole('button', { name: 'Uses result E001 Validate structure', exact: true }).click();
    await expect(graph.getByRole('button', { name: 'E001 Validate structure: success' })).toHaveAttribute('aria-expanded', 'true');
    await expect(graph.getByText('120 events')).toBeVisible();
  }
});
