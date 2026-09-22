import { expect, test } from '@playwright/test';

// Host integration: keyboard selection, real deletion API, socket delivery and canvas.
test('deletes 300 selected cards with one request and keeps the canvas synchronized', async ({ page, request }) => {
  test.setTimeout(90_000);
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en' },
  } })).ok()).toBe(true);
  const ids: string[] = [];
  try {
    let next = 0;
    await Promise.all(Array.from({ length: 8 }, async () => {
      while (next < 300) {
        const index = next++;
        const response = await request.post('/api/nodes', { data: {
          id: `bulk-delete-${index}`, type: 'text', name: `Bulk ${index}`, content: `Text ${index}`,
          position: { x: 250 + index % 20 * 60, y: 220 + Math.floor(index / 20) * 50 },
        } });
        expect(response.ok()).toBe(true);
        ids.push((await response.json()).id);
      }
    }));
    await page.goto('/');
    await expect(page.locator(`[data-card-id="${ids[0]}"]`).first()).toBeVisible();
    await page.evaluate(async ids => {
      // Import the running store to select the complete fixture, including offscreen cards.
      const { useWorldStore } = await import('/src/state/worldStore.ts');
      useWorldStore.getState().selectCards(ids);
    }, ids);
    await page.waitForTimeout(300);
    let singleRequests = 0, batchRequests = 0;
    page.on('request', req => {
      if (req.method() === 'DELETE' && /\/api\/nodes\//.test(req.url())) singleRequests++;
      if (req.method() === 'POST' && req.url().endsWith('/api/nodes/batch-delete')) batchRequests++;
    });
    await page.evaluate(async () => {
      const { useWorldStore } = await import('/src/state/worldStore.ts');
      const probe = { graphUpdates: 0, frameGaps: [] as number[], stop: false, unsubscribe: () => {} };
      (window as any).__bulkDeleteProbe = probe;
      probe.unsubscribe = useWorldStore.subscribe((state, previous) => {
        if (state.cards !== previous.cards || state.edges !== previous.edges) probe.graphUpdates++;
      });
      let previous = performance.now();
      const frame = (now: number) => { probe.frameGaps.push(now - previous); previous = now; if (!probe.stop) requestAnimationFrame(frame); };
      requestAnimationFrame(frame);
    });
    const completed = page.waitForResponse(res => res.url().endsWith('/api/nodes/batch-delete') && res.request().method() === 'POST');
    const started = Date.now();
    await page.keyboard.press('Delete');
    expect((await completed).ok()).toBe(true);
    await expect(page.locator('[data-card-id^="bulk-delete-"]')).toHaveCount(0);
    await expect.poll(async () => page.evaluate(async () => {
      const { useWorldStore } = await import('/src/state/worldStore.ts');
      return useWorldStore.getState().undoStack.at(-1)?.kind;
    })).toBe('cards-deleted');
    const elapsedMs = Date.now() - started;
    const sample = await page.evaluate(() => {
      const probe = (window as any).__bulkDeleteProbe;
      probe.stop = true; probe.unsubscribe();
      const gaps = probe.frameGaps.slice(1).sort((a: number, b: number) => a - b);
      return { graphUpdates: probe.graphUpdates, frames: gaps.length, p95FrameMs: gaps[Math.floor(gaps.length * .95)], maxFrameMs: gaps.at(-1) };
    });
    expect(singleRequests).toBe(0);
    expect(batchRequests).toBe(1);
    expect(sample.graphUpdates).toBeLessThan(30);
    const world = await (await request.get('/api/world')).json();
    expect(world.nodes.filter((node: { id: string }) => ids.includes(node.id))).toEqual([]);
    console.log('BULK_DELETE_PERFORMANCE', JSON.stringify({ cards: ids.length, elapsedMs, singleRequests, batchRequests, ...sample }));
    await page.screenshot({ path: 'test-results/bulk-delete-completed.png' });
  } finally {
    const world = await (await request.get('/api/world')).json();
    const remaining = world.nodes.filter((node: { id: string }) => ids.includes(node.id)).map((node: { id: string }) => node.id);
    if (remaining.length) await request.post('/api/nodes/batch-delete', { data: { node_ids: remaining } });
  }
});

test('undo restores deleted text bodies and links while preserving an unselected Agent', async ({ page, request }) => {
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }) },
  } })).ok()).toBe(true);
  const ids: string[] = [];
  try {
    const agentResponse = await request.post('/api/nodes', { data: { type: 'agent', position: { x: 250, y: 250 } } });
    expect(agentResponse.ok()).toBe(true);
    const agent = await agentResponse.json();
    ids.push(agent.id);
    for (let i = 0; i < 12; i++) {
      const response = await request.post('/api/nodes', { data: { type: 'text', content: `Restore body ${i}`, position: { x: 500 + i % 4 * 130, y: 250 + Math.floor(i / 4) * 140 } } });
      expect(response.ok()).toBe(true);
      const card = await response.json();
      ids.push(card.id);
      expect((await request.post('/api/edges', { data: { source: agent.id, target: card.id, relationship: 'read' } })).ok()).toBe(true);
    }
    await page.goto('/');
    await expect(page.locator(`[data-card-id="${agent.id}"]`).first()).toBeVisible();
    await page.evaluate(async ids => {
      const { useWorldStore } = await import('/src/state/worldStore.ts');
      useWorldStore.getState().selectCards(ids);
    }, ids.slice(1));
    await page.keyboard.press('Delete');
    await expect.poll(async () => (await (await request.get('/api/world')).json()).nodes.length).toBe(1);
    await expect.poll(async () => page.evaluate(async () => {
      const { useWorldStore } = await import('/src/state/worldStore.ts');
      return useWorldStore.getState().undoStack.at(-1)?.kind;
    })).toBe('cards-deleted');
    await page.keyboard.press('Control+z');
    await expect.poll(async () => (await (await request.get('/api/world')).json()).edges.length).toBe(12);
    await expect(page.locator(`[data-card-id="${ids[1]}"]`).first()).toBeVisible();
    for (let i = 0; i < 12; i++) expect(await (await request.get(`/api/resources/${ids[i + 1]}/content`)).text()).toBe(`Restore body ${i}`);
    expect((await request.get(`/api/nodes/${agent.id}`)).ok()).toBe(true);
  } finally {
    const world = await (await request.get('/api/world')).json();
    const remaining = world.nodes.filter((node: { id: string }) => ids.includes(node.id)).map((node: { id: string }) => node.id);
    if (remaining.length) await request.post('/api/nodes/batch-delete', { data: { node_ids: remaining } });
  }
});
