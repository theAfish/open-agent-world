import { expect, test } from '@playwright/test';

test('execution groups stream, archive previous work, and retain their position after reload', async ({ page, request }) => {
  test.setTimeout(90_000);
  expect((await request.post('/api/test/conversation-activity/install?incremental=true')).ok()).toBe(true);
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
    'oaw-progressive-tutorials-v1': JSON.stringify({ version: 1, state: { enabled: false, progress: {} } }),
    'oaw-node-surfaces-v1': null, 'oaw-theme': 'light', 'oaw-canvas-viewport-v1': null,
  } } });
  const agent = await (await request.post('/api/nodes', { data: { type: 'agent', name: 'Review worker' } })).json();
  const room = await (await request.post('/api/nodes', { data: { type: 'conversation', name: 'Activity review', position: { x: 300, y: 250 } } })).json();
  await request.post('/api/edges', { data: { source: agent.id, target: room.id, relationship: 'participate' } });
  const session = (await (await request.get(`/api/conversations/${room.id}`)).json()).sessions[0];
  const base = `/api/conversations/${room.id}/sessions/${session.id}`;
  const errors: string[] = [];
  let workspaceId: string | undefined;
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto('/');
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    const card = page.locator(`[data-card-id="${room.id}"]`);
    const opener = card.getByRole('button', { name: 'Open workspace', exact: true });
    if (await opener.count()) await opener.evaluate((node: HTMLButtonElement) => node.click());
    const surface = page.locator(`[data-workspace-node-id="${room.id}"]`);
    await expect(surface).toBeVisible();
    await surface.getByLabel('Conversation message', { exact: true }).fill('Review the changes and report what you find.');
    await surface.getByRole('button', { name: 'Send message', exact: true }).click();
    const groups = surface.locator('.conversation-execution');
    await expect(groups).toHaveCount(1);
    await expect(groups.locator('.run-tool')).toHaveCount(1);
    await expect(groups.locator('.run-tool-name')).toHaveText('write_session_summary');
    await expect(surface.locator('.conversation-history')).toHaveCount(0);
    await expect(surface.getByRole('button', { name: 'Refresh', exact: true })).toHaveCount(0);
    await expect(groups.locator('.run-tool-summary')).not.toContainText('src/module-0.ts');
    await expect(groups.locator('[aria-label="Running"]')).toHaveCount(2);
    const runningTime = groups.locator('.run-tool .conversation-duration');
    await expect(runningTime).toHaveText(/\d+\.\d+s/);
    const firstTime = await runningTime.textContent();
    await expect(runningTime).not.toHaveText(firstTime!);
    const snapshot = await (await request.get(`${base}/timeline`)).json();
    const runId = snapshot.active_runs[0].run_id;
    for (let index = 0; index < 7; index++) {
      await request.post(`/api/test/conversation-activity/${runId}/step/0/${index}`);
      await expect(groups.locator('.run-tool')).toHaveCount(index + 2);
    }
    await expect(surface.locator('.conversation-speaker-name')).toHaveText('Review worker');
    await expect(surface.locator('.conversation-speaker-avatar')).toHaveCount(1);
    const narration = surface.locator('.conversation-message-bubble').first();
    await expect(surface.locator('.conversation-message-bubble')).toHaveCount(1);
    expect(await narration.evaluate(node => getComputedStyle(node).borderTopWidth)).toBe('1px');
    expect(await groups.evaluate(node => Boolean(node.closest('.conversation-message-bubble')))).toBe(false);
    expect((await groups.boundingBox())!.y - (await narration.boundingBox())!.y - (await narration.boundingBox())!.height).toBeLessThanOrEqual(14);
    await expect(groups.locator('.conversation-execution-summary')).not.toContainText('Review worker');
    await expect(groups.locator('.conversation-execution-summary')).not.toContainText('Execution details');
    const summaryText = groups.locator('.conversation-execution-summary > span').first();
    const chevron = groups.locator('.conversation-execution-chevron');
    expect((await chevron.boundingBox())!.x).toBeGreaterThan((await summaryText.boundingBox())!.x + (await summaryText.boundingBox())!.width);
    const stop = surface.getByRole('button', { name: 'Stop', exact: true });
    expect((await stop.boundingBox())!.width).toBeLessThan(120);
    const scroll = groups.locator('.conversation-execution-scroll');
    const size = await scroll.evaluate(node => ({ height: node.clientHeight, total: node.scrollHeight, top: node.scrollTop }));
    expect(size.height).toBeLessThanOrEqual(110);
    expect(size.total).toBeGreaterThan(size.height);
    expect(size.top + size.height).toBeGreaterThanOrEqual(size.total - 2);
    const outer = surface.locator('.workspace-transcript');
    const outerTop = await outer.evaluate(node => node.scrollTop);
    await scroll.evaluate(node => { node.scrollTop = 0; node.dispatchEvent(new Event('scroll')); });
    await expect(groups.getByRole('button', { name: 'Latest activity' })).toBeVisible();
    await request.post(`/api/test/conversation-activity/${runId}/step/0/7`);
    await expect(groups.locator('.run-tool')).toHaveCount(9);
    expect(await scroll.evaluate(node => node.scrollTop)).toBe(0);
    expect(await outer.evaluate(node => node.scrollTop)).toBe(outerTop);
    await groups.getByRole('button', { name: 'Latest activity' }).click();
    for (let index = 8; index < 11; index++) {
      await request.post(`/api/test/conversation-activity/${runId}/step/0/${index}`);
      await expect(groups.locator('.run-tool')).toHaveCount(index + 2);
    }
    const capture = async (prefix = '', folded = false) => { for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => { document.documentElement.dataset.theme = theme; }, theme);
      for (const width of [1060, 600, 360]) {
        const camera = await surface.evaluate((element, width) => {
          const host = element as HTMLElement;
          host.style.width = `${width}px`; host.style.height = '720px';
          const frame = host.closest<HTMLElement>('.world-card');
          if (frame) { frame.style.width = `${width}px`; frame.style.height = '720px'; }
          const matrix = new DOMMatrix(getComputedStyle(host.closest('.react-flow__node')!).transform);
          return { x: 30 - matrix.m41, y: 30 - matrix.m42 };
        }, width);
        const style = await page.addStyleTag({ content: `.react-flow__viewport { transform: translate(${camera.x}px, ${camera.y}px) scale(1) !important; }` });
        const target = prefix === 'system-' ? surface.locator('.conversation-system-notice')
          : folded ? surface.locator('.conversation-history') : groups.last();
        await expect(target).toBeVisible();
        const bounds = await target.evaluate(node => ({ w: node.clientWidth, sw: node.scrollWidth }));
        expect(bounds.sw).toBeLessThanOrEqual(bounds.w + 1);
        expect(await outer.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
        if (prefix === 'expanded-') await outer.evaluate(node => { node.scrollTop = 0; });
        if (prefix === 'system-') await outer.evaluate(node => { node.scrollTop = node.scrollHeight; });
        await page.mouse.move(1400, 950);
        await surface.screenshot({ path: `../.outputs/conversation-activity/${prefix}${theme}-${width}.png` });
        await style.evaluate(node => node.remove());
      }
    } };
    await capture();
    await request.post(`/api/test/conversation-activity/${runId}/advance/0`);
    await expect(groups).toHaveCount(2);
    await expect(groups.nth(0).getByRole('button', { name: /tool calls/ })).toHaveAttribute('aria-expanded', 'false');
    for (let index = 0; index < 7; index++) {
      await request.post(`/api/test/conversation-activity/${runId}/step/1/${index}`);
      await expect(groups.nth(1).locator('.run-tool')).toHaveCount(index + 2);
    }
    await expect(groups.nth(1).locator('.run-tool')).toHaveCount(8);
    const progress = surface.getByText('The files agree. I am checking the implementation next.', { exact: true });
    await expect(progress).toBeVisible();
    await expect(surface.locator('.conversation-message-bubble')).toHaveCount(2);
    await expect(surface.locator('.conversation-speaker-avatar')).toHaveCount(1);
    await expect(surface.locator('.conversation-speaker-name')).toHaveCount(1);
    expect((await groups.nth(0).boundingBox())!.y).toBeLessThan((await progress.boundingBox())!.y);
    expect((await progress.boundingBox())!.y).toBeLessThan((await groups.nth(1).boundingBox())!.y);
    await capture('between-');
    const ids = await groups.evaluateAll(nodes => nodes.map(node => node.getAttribute('data-execution-id')));
    const restoredDirectory = page.waitForResponse(response => response.url().includes(`/api/runs/${runId}/activity?`) && response.ok());
    await page.reload();
    await restoredDirectory;
    await expect(groups).toHaveCount(2);
    await expect(progress).toBeVisible();
    // Metadata and the live checkpoint must agree about which events are visible.
    await expect.poll(async () => (await (await request.get(`/api/runs/${runId}/activity`)).json()).total).toBe(21);
    await expect(surface.locator('.conversation-message-bubble')).toHaveCount(2);
    await expect(surface.locator('.conversation-history')).toHaveCount(0);
    await expect(surface.getByRole('button', { name: 'Refresh', exact: true })).toHaveCount(0);
    await capture('restored-');
    expect(await groups.evaluateAll(nodes => nodes.map(node => node.getAttribute('data-execution-id')))).toEqual(ids);
    await request.post(`/api/test/conversation-activity/${runId}/advance/1`);
    await expect(surface.getByText('Review complete. One check needs attention.', { exact: true })).toBeVisible();
    const workLog = surface.locator('.conversation-history');
    const finalReply = surface.getByText('Review complete. One check needs attention.', { exact: true });
    const expectWorkBeforeReply = async () => {
      const work = await workLog.boundingBox();
      const reply = await finalReply.boundingBox();
      expect(work!.y + work!.height).toBeLessThanOrEqual(reply!.y);
    };
    await expect(workLog).toHaveCount(1);
    await expect(workLog).not.toHaveAttribute('open');
    await expect(surface.locator('.conversation-message-bubble')).toHaveCount(1);
    await expect(groups).toHaveCount(0);
    await expect(surface.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0);
    await expectWorkBeforeReply();
    await capture('completed-', true);
    await page.reload();
    await expect(workLog).toHaveCount(1);
    await expect(workLog).not.toHaveAttribute('open');
    await expectWorkBeforeReply();
    const toggle = workLog.locator(':scope > summary');
    await toggle.focus();
    await page.keyboard.press('Enter');
    await expect(groups).toHaveCount(2);
    await expect(progress).toBeVisible();
    await expect(surface.locator('.conversation-message-bubble')).toHaveCount(3);
    await expect(groups.nth(1)).toContainText('1 failed');
    await expect(groups.nth(0).locator('.run-tool')).toHaveCount(12);
    await expect(groups.locator('.run-tool .conversation-duration')).toHaveCount(20);
    await expectWorkBeforeReply();
    const durations = await groups.locator('.run-tool .conversation-duration').allTextContents();
    const firstViewport = groups.nth(0).locator('.conversation-execution-scroll');
    expect(await firstViewport.evaluate(node => node.clientHeight)).toBeGreaterThan(300);
    expect(await firstViewport.evaluate(node => node.scrollHeight - node.clientHeight)).toBeLessThanOrEqual(1);
    await groups.nth(0).locator('.run-tool > summary').first().click();
    await expect(groups.nth(0).getByText(/Checked module 0/)).toBeVisible();
    const outputSection = groups.nth(0).locator('.run-tool').first().getByRole('region', { name: 'Output' });
    const inputSection = groups.nth(0).locator('.run-tool').first().getByRole('region', { name: 'Input' });
    await expect(inputSection.getByRole('table')).toHaveCount(1);
    await expect(inputSection.locator('th[title="summary"]')).toHaveAttribute('rowspan', '6');
    await expect(inputSection.getByText('Keep the approved plan.', { exact: true })).toBeVisible();
    await expect(inputSection.locator('details, ol, button')).toHaveCount(0);
    await expect(groups.locator('.run-tool-details')).not.toContainText('oaw_invoke_tool');
    await expect(groups.locator('.run-tool-details')).not.toContainText('operation:read_file');
    await expect(groups.getByText('Raw JSON', { exact: true })).toHaveCount(0);
    await expect(outputSection.getByRole('rowheader', { name: 'exit code' })).toBeVisible();
    await expect(outputSection.locator('th[title="lines"]')).toHaveAttribute('rowspan', '30');
    await expect(outputSection.getByText('Line 29: detailed inspection result', { exact: true })).toBeVisible();
    const output = outputSection.getByRole('table');
    expect(await output.evaluate(node => node.clientHeight)).toBeGreaterThan(500);
    expect(await output.evaluate(node => node.scrollHeight - node.clientHeight)).toBeLessThanOrEqual(1);
    await capture('expanded-');
    // Long nested disclosures keep each ancestor reachable without overlapping.
    const toolHeading = groups.nth(0).locator('.run-tool > summary').first();
    const groupHeading = groups.nth(0).locator('.conversation-execution-summary');
    const scrollPastHeading = async (heading: typeof toolHeading) => {
      await heading.evaluate(node => {
        const viewport = node.closest<HTMLElement>('.workspace-transcript')!;
        const bounds = viewport.getBoundingClientRect();
        const scale = bounds.height / viewport.offsetHeight;
        viewport.scrollTop += (node.parentElement!.getBoundingClientRect().top - bounds.top) / scale + 450;
      });
    };
    const expectPinned = async (headings: (typeof toolHeading)[]) => {
      await expect.poll(async () => {
        let top = await headings[0].evaluate(node => node.closest('.workspace-transcript')!.getBoundingClientRect().top);
        for (const heading of headings) {
          const bounds = (await heading.boundingBox())!;
          if (Math.abs(bounds.y - top) > 2) return false;
          if (!await heading.evaluate(node => {
            const bounds = node.getBoundingClientRect();
            return node.contains(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2));
          })) return false;
          top = bounds.y + bounds.height;
        }
        return true;
      }).toBe(true);
    };
    await scrollPastHeading(toolHeading);
    await expectPinned([toggle, groupHeading, toolHeading]);
    await toolHeading.click();
    await expect(groups.nth(0).locator('.run-tool').first()).not.toHaveAttribute('open');
    await expectPinned([toggle, groupHeading, toolHeading]);
    await toolHeading.click();
    await scrollPastHeading(toolHeading);
    await groupHeading.focus();
    await page.keyboard.press('Enter');
    await expect(groupHeading).toHaveAttribute('aria-expanded', 'false');
    await expectPinned([toggle, groupHeading]);
    await groupHeading.click();
    await scrollPastHeading(toolHeading);
    await toggle.click();
    await expect(workLog).not.toHaveAttribute('open');
    await expect(toggle).toBeInViewport();
    await page.reload();
    await workLog.locator(':scope > summary').click();
    await expect(groups.locator('.run-tool .conversation-duration')).toHaveCount(20);
    expect(await groups.locator('.run-tool .conversation-duration').allTextContents()).toEqual(durations);
    // Only the notice fixture is synthetic here; the activity/history above is
    // restored through the actual host. Verify long job identities in the UI.
    await page.route(`**${base}/timeline*`, async route => {
      const response = await route.fetch();
      const body = await response.json();
      const last = body.items.at(-1);
      body.items.push({ id: 'monitoring-notice', conversation_id: room.id, session_id: session.id,
        sender_kind: 'system', sender_name: 'System', mention_agent_ids: [], kind: 'run_outcome',
        sequence: (last?.sequence ?? 0) + 1, created_at: new Date(Date.parse(last.created_at) + 1).toISOString(),
        content: 'Review worker handed off 1 background job for automatic monitoring. The conversation resumes when results are available.\n\n'
          + 'melt_quench_md · matcreator.bohrium-batch\nExternal ID: remote-job-123\nJob ID: 6a22cb7d-8ec0-4ad8-9d84-23a319a20fa0\n\nFind these jobs by name or ID in Jobs.' });
      await route.fulfill({ response, json: body });
    });
    await page.reload();
    await expect(surface.locator('.conversation-system-label')).toHaveText('System');
    await expect(surface.locator('.conversation-system-notice')).toContainText('remote-job-123');
    await capture('system-');
    // Provider thinking uses the same pinning and collapse behavior. Keep this
    // visual fixture separate from the real tool archive exercised above.
    const thinkingText = Array.from({ length: 60 }, (_, i) => `Observation ${i + 1}: inspect the saved result before starting more work.`).join('\n\n');
    await page.route(`**/api/runs/${runId}/activity*`, route => route.fulfill({ json: {
      items: [{ id: 'thinking:fixture', type: 'agent_progress', kind: 'model_reasoning', role: 'thinking_agent',
        provider_message_id: 'fixture', timestamp: snapshot.active_runs[0].started_at, streaming: false, preview_truncated: true }],
      total: 1, next_offset: null,
    } }));
    await page.route(`**/api/runs/${runId}/reasoning/fixture`, route => route.fulfill({ json: {
      run_id: runId, provider_message_id: 'fixture', text: thinkingText, streaming: false, preview_truncated: false,
      created_at: snapshot.active_runs[0].started_at, updated_at: snapshot.active_runs[0].started_at,
    } }));
    await page.reload();
    await toggle.click();
    const thinking = surface.locator('.run-model-reasoning');
    const thinkingHeading = thinking.locator('summary');
    await thinkingHeading.click();
    await expect(thinking.locator('pre')).toHaveText(thinkingText);
    await scrollPastHeading(thinkingHeading);
    await expectPinned([toggle, thinkingHeading]);
    await surface.screenshot({ path: '../.outputs/conversation-activity/sticky-thinking.png' });
    await thinkingHeading.focus();
    await page.keyboard.press('Enter');
    await expect(thinking).not.toHaveAttribute('open');
    await expect(thinkingHeading).toBeInViewport();
    // A saved, detached pane mounts its transcript after the owning component.
    // Restore it with the already-selected session, so no session change masks
    // a missed DOM attachment. Headers must also be hittable, not just aligned.
    const studio = (await (await request.post('/api/legion-groups', { data: {
      name: 'Activity studio', node_ids: [room.id],
    } })).json())[0];
    workspaceId = studio.id;
    await request.patch(`/api/nodes/${studio.id}`, { data: { config: { workspace_layout: {
      version: 2, hidden_sections: [], root: { kind: 'pane', view: { card_id: room.id, section_id: 'conversation' } },
    } } } });
    await page.reload();
    await page.locator(`[data-card-id="${studio.id}"]`).getByRole('button', { name: 'Workspace mode', exact: true })
      .evaluate((button: HTMLButtonElement) => button.click());
    const detached = page.getByRole('dialog', { name: 'Activity studio workspace mode' });
    const detachedWork = detached.locator('.conversation-history > summary');
    const detachedThinking = detached.locator('.run-model-reasoning > summary');
    await detachedWork.click();
    await detachedThinking.click();
    await expect(detached.locator('.run-model-reasoning > pre')).toHaveText(thinkingText);
    await scrollPastHeading(detachedThinking);
    await detached.screenshot({ path: '../.outputs/conversation-activity/sticky-detached.png' });
    await expectPinned([detachedWork, detachedThinking]);
    await detachedThinking.click();
    await expect(detached.locator('.run-model-reasoning')).not.toHaveAttribute('open');
    await expect(detachedThinking).toBeInViewport();
    expect(errors).toEqual([]);
  } finally {
    await request.delete(`/api/nodes/${room.id}`);
    if (workspaceId) await request.delete(`/api/nodes/${workspaceId}`);
    await request.delete(`/api/nodes/${agent.id}`);
    await request.post('/api/test/conversation-input/restore');
  }
});
