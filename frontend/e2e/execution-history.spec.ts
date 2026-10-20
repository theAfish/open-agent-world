import { expect, test } from '@playwright/test';
import type { ExecutionEdge, ExecutionNode } from '../src/plugins/executionSources';

// Host-persisted, Pack-shaped activities; no model or remote compute is needed.
function history(count: number) {
  const nodes: ExecutionNode[] = [];
  for (let n = 1; n <= count; n++) {
    const id = 'agent:execution_' + n, label = 'E' + String(n).padStart(2, '0');
    nodes.push({ id, label, kind: 'agent', role: 'execution', execution_id: id, sequence: n * 10,
      actor_id: 'executor-' + n % 3, actor_label: ['Atlas', 'Nova', 'Mira'][n % 3], workstream_id: id,
      status: n === count ? 'running' : 'success' });
    for (let step = 1; step <= 9; step++) nodes.push({
      id: id + ':step_' + step, label: label + ' step ' + step, kind: 'agent', role: 'step',
      execution_id: id, parent_id: id, plan_id: 'plan_' + n, sequence: n * 10 + step,
      actor_id: 'executor-' + n % 3, actor_label: ['Atlas', 'Nova', 'Mira'][n % 3], workstream_id: id,
      status: n === count ? 'running' : 'success', artifact_refs: ['oaw:artifact:outputs:' + n + '_' + step],
    });
  }
  return nodes;
}

test('complete history, live partial publication and reload retain old events', async ({ page, request }) => {
  test.setTimeout(100_000);
  await page.setViewportSize({ width: 1600, height: 1000 });
  const deployed = await request.post('/api/legions/presets/matcreator.native-research/instances', { data: {} });
  expect(deployed.status(), await deployed.text()).toBe(201);
  const ids = (await deployed.json()).node_ids;
  const session = (await (await request.get('/api/conversations/' + ids.conversation)).json()).sessions[0].id;
  const publish = async (data: object) => {
    const response = await request.post('/api/test/execution/' + ids.agent + '/' + session, { data });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  const initial = { history_id: 'pack-test-history', retention: 'history', revision: 1, phase: 'idle', nodes: history(20), edges: [] as ExecutionEdge[] };
  const { run_id } = await publish(initial);
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
      'oaw-active-workspace-v1': JSON.stringify({ version: 0, state: { activeId: ids.group } }),
    },
  } })).ok()).toBe(true);
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  const graph = page.getByRole('region', { name: 'Execution graph', exact: true });
  await expect(graph.getByText('200 events')).toBeVisible({ timeout: 30_000 });
  await expect(graph.locator('.execution-fold, .execution-expand')).toHaveCount(0);
  const search = graph.getByRole('textbox', { name: 'Find event' });
  const locate = async (label: string) => { await search.fill(label); await search.press('Enter'); };
  await locate('E20 step 9');
  await expect(graph.getByRole('button', { name: 'E20 step 9: running', exact: true })).toHaveAttribute('aria-expanded', 'true');
  const dependencies: ExecutionEdge[] = [1, 9, 17].map(n => ({ id: 'data-' + n,
    source: 'agent:execution_' + n + ':step_1', target: 'agent:execution_20:step_9', kind: 'data',
    evidence: 'artifact_inspected', artifact_ref: 'oaw:artifact:outputs:' + n + '_1' }));
  await publish({ ...initial, run_id, revision: 2, edges: dependencies });
  await expect(graph.locator('.execution-anchors:not(.execution-continuity) button')).toHaveCount(3);
  await expect(graph.locator('[data-graph-edge][data-kind="data"]')).toHaveCount(0);
  await graph.getByRole('button', { name: 'Uses result E01 step 1', exact: true }).click();
  await expect(graph.getByRole('button', { name: 'E01 step 1: success', exact: true })).toHaveAttribute('aria-expanded', 'true');
  await graph.getByRole('button', { name: 'Used by E20 step 9', exact: true }).click();
  await graph.screenshot({ path: 'test-results/project-history-sparse.png' });
  const large = { ...initial, run_id, revision: 3, phase: 'running', nodes: history(120), edges: dependencies };
  await publish(large);
  await expect(graph.getByText('1200 events')).toBeVisible();
  const viewport = graph.getByLabel('Execution history', { exact: true });
  await locate('E120 step 9');
  await expect(graph.getByRole('button', { name: 'E120 step 9: running', exact: true })).toHaveAttribute('aria-expanded', 'true');
  const metrics = await viewport.evaluate(element => ({ rows: element.querySelectorAll('.execution-vertex').length,
    width: element.clientWidth, scrollWidth: element.scrollWidth, height: element.scrollHeight }));
  expect(metrics.rows).toBeLessThan(25); expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.width);
  expect(metrics.height).toBeGreaterThan(1200 * 54);
  // A history publication containing only changed/new events must persist
  // its failed attempt and all 1,200 old identities, also after reloading.
  await publish({ ...large, revision: 4, nodes: [
    { ...large.nodes.at(-1)!, status: 'failed' },
    { id: 'retry', label: 'Retry validation', kind: 'agent', status: 'running', sequence: 1210, retry_of: large.nodes.at(-1)!.id },
  ], edges: [{ id: 'retry-edge', source: large.nodes.at(-1)!.id, target: 'retry', kind: 'retry' }] });
  await expect(graph.getByText('1201 events')).toBeVisible();
  await expect(graph.getByRole('button', { name: 'E120 step 9: failed', exact: true })).toHaveAttribute('aria-expanded', 'true');
  await graph.screenshot({ path: 'test-results/project-history-large.png' });
  await page.reload();
  await expect(graph.getByText('1201 events')).toBeVisible();
  await locate('E120 step 9');
  await expect(graph.getByRole('button', { name: 'E120 step 9: failed', exact: true })).toBeVisible();
  await expect(graph.getByRole('button', { name: 'Retry validation: running', exact: true })).toBeVisible();
  expect(errors).toEqual([]);
  const nodes = await (await request.get('/api/nodes')).json();
  expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.map((node: { id: string }) => node.id) } })).ok()).toBe(true);
});
