import { expect, test } from '@playwright/test';

test('research tasks adapt to pane width and preserve detail actions', async ({ page, request }) => {
  await page.setViewportSize({ width: 1500, height: 1000 });
  const response = await request.post('/api/nodes', { data: { type: 'matcreator.tasks', name: 'Task layout', position: { x: 800, y: 450 } } });
  expect(response.status()).toBe(201);
  const node = await response.json();
  const url = `/api/nodes/${node.id}`;
  try {
    const doc = await (await request.get(url + '/document')).json();
    const titles = ['VASP relaxation', 'Phonon calculation', 'Cluster allocation', 'Charge density validation', 'POTCAR validation', 'Environment setup', 'Structure validation', 'Input validation'];
    const statuses = ['running', 'running', 'blocked', 'pending', 'done', 'done', 'done', 'done'];
    const created = await request.post(url + '/actions/create_plan', { data: { expected_revision: doc.revision, arguments: {
      title: 'VASP test', goal: 'Validate the complete VASP workflow, including environment setup, pseudopotentials, relaxation and charge density.', session_id: 'research-session-29496',
      tasks: titles.map((title, i) => ({ id: String(i), title, status: statuses[i], description: 'Full research task detail retained for inspection.', result: i === 0 ? 'bohr job 29496' : i === 2 ? 'Waiting for compute allocation' : i >= 4 ? 'Verified successfully' : '', outputs: i >= 4 ? ['vasp/validation.json'] : [], depends_on: [] })),
    } } });
    expect(created.ok()).toBe(true);
    await page.goto('/');
    const card = page.locator(`[data-card-id="${node.id}"]`);
    await card.locator('.card-kind-icon').click();
    await card.getByRole('button', { name: 'Open workspace', exact: true }).click();
    const board = page.getByRole('region', { name: 'Research task board', exact: true });
    await expect(board.getByRole('heading', { name: 'VASP test' })).toBeVisible();
    await expect.poll(async () => (await board.boundingBox())!.x).toBeGreaterThan(0);
    // Keep the viewport fixed: exercise the actual plugin container query in its host.
    const size = async (width: number) => {
      await board.evaluate((element, width) => { (element as HTMLElement).style.width = `${width}px`; }, width);
      // Container queries use layout pixels; canvas zoom scales screen bounds.
      await expect(board).toHaveCSS('width', `${width}px`);
    };
    for (const [label, width] of [['narrow', 320], ['medium', 600], ['wide', 1000]] as const) {
      await size(width);
      await expect(board.locator('.mc-task-sections')).toHaveCSS('grid-template-columns', `${width - 28}px`);
      await expect(board.getByRole('button', { name: 'Input validation', exact: true })).toBeVisible();
      await expect(board.locator('.mc-task-item')).toHaveCount(8);
      await expect.poll(() => board.locator('.mc-task-grid').last().evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(width >= 488 ? 2 : 1);
      await expect(board.locator('.mc-task-subtitle')).toHaveCSS('text-overflow', 'ellipsis');
      await expect(board.getByRole('button', { name: 'Open board', exact: true })).toBeVisible({ visible: width >= 848 });
      expect(await board.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
      await board.screenshot({ path: `test-results/research-tasks-${label}.png` });
    }
    await board.getByRole('button', { name: 'Open board', exact: true }).click();
    await expect(board.getByRole('button', { name: 'Input validation', exact: true })).toBeVisible();
    await expect.poll(() => board.locator('.mc-task-sections').evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(4);
    await board.screenshot({ path: 'test-results/research-tasks-kanban.png' });
    await size(320);
    await expect(board.locator('.mc-task-sections')).toHaveCSS('grid-template-columns', '292px');
    await board.getByRole('button', { name: 'Input validation', exact: true }).click();
    await expect(board.getByRole('article', { name: 'Task detail' })).toBeVisible();
    await expect(board.getByRole('textbox')).toHaveCount(0);
    await expect(board.locator('.mc-task-grid')).toHaveCount(0);
    await expect(board.getByText('Full research task detail retained for inspection.', { exact: true })).toBeVisible();
    await board.screenshot({ path: 'test-results/research-task-detail.png' });
    await board.getByRole('button', { name: 'Edit', exact: true }).click();
    await expect(board.getByLabel('Task details', { exact: true })).toHaveValue('Full research task detail retained for inspection.');
    await expect(board.getByLabel('Output paths (one per line)')).toHaveValue('vasp/validation.json');
    await board.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(board.getByRole('article', { name: 'Task detail' })).toBeVisible();
    await board.getByRole('button', { name: '‹ Tasks', exact: true }).click();
    await board.getByLabel('Plan details and actions').click();
    await expect(board.getByText('Session: research-session-29496', { exact: true })).toHaveCount(0);
    await board.getByRole('button', { name: 'Refresh', exact: true }).click();
    await board.getByLabel('Plan details and actions').press('Escape');
    await expect(board.getByRole('button', { name: 'Refresh', exact: true })).toBeHidden();
    await size(600);
    const current = await (await request.get(url + '/document')).json();
    const completed = await request.post(url + '/actions/create_plan', { data: { expected_revision: current.revision, arguments: {
      title: 'VASP verification', goal: 'Environment, inputs and outputs verified.', tasks: [...titles, 'Final report'].map((title, i) => ({
        id: String(i), title, status: 'done', result: 'Verified', description: '', outputs: [], depends_on: [],
      })),
    } } });
    expect(completed.ok()).toBe(true);
    await board.getByLabel('Plan details and actions').click();
    await expect(board.getByLabel('Research plans')).toContainText('VASP verification');
    await board.getByLabel('Research plans').selectOption({ label: 'VASP verification' });
    await expect(board.getByRole('heading', { name: 'VASP verification' })).toBeVisible();
    await expect(board.locator('.mc-task-section')).toHaveCount(1);
    await expect(board.locator('.mc-task-item')).toHaveCount(9);
    // Escape closes plan details without collapsing the host window.
    await expect.poll(async () => (await card.boundingBox())!.width).toBeGreaterThan(1000);
    await board.evaluate(element => { element.scrollTop = 0; });
    await expect(board.getByRole('button', { name: 'Final report', exact: true })).toBeInViewport();
    await board.screenshot({ path: 'test-results/research-tasks-completed.png' });
    await page.getByRole('button', { name: 'Use dark theme', exact: true }).click();
    await board.screenshot({ path: 'test-results/research-tasks-completed-dark.png' });
    await board.getByRole('button', { name: 'Final report', exact: true }).click();
    await board.getByRole('button', { name: 'Edit', exact: true }).click();
    await board.getByLabel('Task title', { exact: true }).fill('Validate the final research report against all simulation outputs and convergence criteria');
    await board.getByRole('button', { name: 'Save task', exact: true }).click();
    await board.getByRole('button', { name: '‹ Tasks', exact: true }).click();
    await expect.poll(() => board.locator('.mc-task-grid').evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(1);
    await size(800);
    await expect.poll(() => board.locator('.mc-task-grid').evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(2);
  } finally { await request.delete(url); }
});
