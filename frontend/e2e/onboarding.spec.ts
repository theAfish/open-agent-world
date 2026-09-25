import { resetTutorialProfile } from './tutorial-profile';
import { prepareTutorialDeck } from './tutorial-deck';
import { buildTutorialLegion } from './tutorial-legion';
import { expect, test, type Page } from '@playwright/test';

test.describe('canvas onboarding', () => {
  test.use({ actionTimeout: 10_000 });
  let originalIds: Set<string>;
  test.beforeEach(async ({ page, request }) => {
    await resetTutorialProfile(request);
    originalIds = new Set((await (await request.get('/api/nodes')).json()).map((node: { id: string }) => node.id));
    await page.emulateMedia({ reducedMotion: 'reduce' });
  });
  test.afterEach(async ({ request }) => {
    const cards = await (await request.get('/api/nodes')).json();
    for (const card of cards) if (!originalIds.has(card.id)) await request.delete(`/api/nodes/${card.id}`);
  });
  const at = async (page: Page, step: string) => expect(page.getByRole('region', { name: 'Tutorial guide' })).toHaveAttribute('data-step', step);
  const move = async (page: Page, card: ReturnType<Page['locator']>, dx: number, dy: number) => {
    const icon = card.locator('.card-kind-icon');
    await expect(icon).toBeVisible();
    const box = (await icon.boundingBox())!;
    const x = box.x + box.width / 2, y = box.y + box.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + dx, y + dy, { steps: 15 });
    await page.mouse.up();
  };

  test('welcome choices persist and a blueprint guides missing model setup', async ({ page, request }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Open Agent World' })).toBeVisible();
    await page.screenshot({ path: 'test-results/onboarding-welcome-light.png' });
    await page.getByRole('button', { name: 'Use dark theme' }).click();
    await page.screenshot({ path: 'test-results/onboarding-welcome-dark.png' });
    await page.getByRole('button', { name: 'Start Empty', exact: true }).click();
    await expect(page.locator('.onboarding-layer')).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Open Agent World' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Replay Tutorial', exact: true }).click();
    await at(page, 'enter');
    await page.getByRole('button', { name: 'Skip tutorial', exact: true }).click();
    await page.goto('about:blank');
    await resetTutorialProfile(request);
    await page.goto('/');
    await page.getByRole('button', { name: /^General assistant/ }).click();
    await expect(page.getByRole('dialog', { name: 'Settings', exact: true })).toBeVisible();
    expect((await (await request.get('/api/nodes')).json()).every((card: { minister?: unknown }) => !card.minister)).toBe(true);
    await expect(page.locator('.onboarding-layer')).toHaveCount(0);
  });

  test('the logo walks into a smaller canvas and the hint remains usable after resize', async ({ page }) => {
    await page.setViewportSize({ width: 800, height: 640 });
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Open Agent World' })).toBeVisible();
    const logoRing = (await page.locator('.onboarding-logo-ring').boundingBox())!;
    const logoCharacter = (await page.locator('.tutorial-mascot').boundingBox())!;
    expect(logoCharacter.x).toBeCloseTo(logoRing.x, 0);
    expect(logoCharacter.y).toBeCloseTo(logoRing.y, 0);
    await page.screenshot({ path: 'test-results/onboarding-welcome-small.png' });
    const art = page.locator('.tutorial-mascot svg');
    const original = await art.boundingBox();
    await page.getByRole('button', { name: /^Start Tutorial/ }).click();
    await at(page, 'enter');
    await expect(page.locator('.tutorial-guide')).toHaveAttribute('data-moving', 'false');
    const arrived = await art.boundingBox();
    expect(Math.hypot(arrived!.x - original!.x, arrived!.y - original!.y)).toBeGreaterThan(60);
    await page.screenshot({ path: 'test-results/onboarding-entrance-small.png' });
    await page.setViewportSize({ width: 740, height: 620 });
    const hint = page.getByRole('region', { name: 'Tutorial guide' });
    await expect.poll(async () => {
      const box = (await hint.boundingBox())!;
      return box.x >= 0 && box.y >= 0 && box.x + box.width <= 740 && box.y + box.height <= 620;
    }).toBe(true);
    await page.getByRole('button', { name: 'Minimize tutorial hint' }).click();
    await expect(page.getByRole('button', { name: 'Let’s go', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Show tutorial hint' }).click();
    await page.getByRole('button', { name: 'Let’s go', exact: true }).click();
    await at(page, 'pan');
    await page.getByRole('button', { name: 'Skip tutorial', exact: true }).click();
    await expect(page.locator('.onboarding-layer')).toHaveCount(0);
  });

  for (const motion of ['reduce', 'no-preference'] as const) test(`walks through real navigation, cards, capabilities, glue and Minister (${motion})`, async ({ page, request }) => {
    test.setTimeout(150_000);
    await page.emulateMedia({ reducedMotion: motion });
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/');
    await page.getByRole('button', { name: /^Start Tutorial/ }).click();
    await at(page, 'enter');
    await page.getByRole('button', { name: 'Let’s go', exact: true }).click();
    await at(page, 'pan');
    await page.mouse.move(160, 140); await page.mouse.down(); await page.mouse.move(280, 190, { steps: 10 }); await page.mouse.up();
    await at(page, 'zoom');
    await page.mouse.move(170, 200); await page.mouse.wheel(0, -240);
    await at(page, 'deck');
    await prepareTutorialDeck(page);
    await at(page, 'place-demo');
    await page.getByRole('button', { name: 'Show me', exact: true }).click();
    await page.locator('.tutorial-next').filter({ hasText: 'Continue' }).click();
    await at(page, 'place');
    // A reload resumes rather than restarting or losing ownership of the demo.
    await page.reload();
    await page.getByRole('button', { name: 'Resume', exact: true }).click();
    await at(page, 'place');
    const deck = page.getByRole('complementary', { name: 'Active card deck' });
    const place = async (label: string, x: number, y: number) => {
      await deck.hover();
      await expect.poll(async () => deck.locator('.deck-stage').evaluate(element => getComputedStyle(element).opacity)).toBe('1');
      await expect.poll(async () => deck.locator('.deck-stage').evaluate(element => element.getAnimations().some(animation => animation.playState === 'running'))).toBe(false);
      // Like a user, choose a clear patch in the current viewport. The tutorial
      // focuses different subjects, so fixed screen coordinates can stack cards.
      let point: { x: number; y: number } | undefined;
      await expect.poll(async () => {
        point = await page.evaluate(({ x, y }) => {
        const viewport = document.querySelector('.world-canvas .react-flow__viewport')!;
        const zoom = new DOMMatrix(getComputedStyle(viewport).transform).a;
        const occupied = [...document.querySelectorAll('.world-canvas .react-flow__node, .tutorial-bubble, .component-palette, .top-bar, .map-tools, .world-controls')]
          .map(element => element.getBoundingClientRect()).filter(rect => rect.width && rect.height);
        const candidates = [{ x, y }, ...Array.from({ length: 11 }, (_, i) => 120 + i * 45)
          .flatMap(y => Array.from({ length: 23 }, (_, i) => 90 + i * 50).map(x => ({ x, y })))];
        return candidates.find(point => {
          const left = point.x - 64 * zoom, top = point.y - 102 * zoom, right = left + 224 * zoom, bottom = top + 300 * zoom;
          return left > 20 && top > 20 && right < innerWidth - 20 && bottom < innerHeight - 120
            && !occupied.some(rect => left < rect.right + 25 && right > rect.left - 25 && top < rect.bottom + 25 && bottom > rect.top - 25);
        });
        }, { x, y });
        return Boolean(point);
      }, { message: 'There is a clear patch to place the next card after the camera settles' }).toBe(true);
      const source = deck.getByRole('button', { name: `Place ${label}`, exact: true });
      await source.hover();
      const from = (await source.boundingBox())!;
      await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
      await page.mouse.down();
      await page.mouse.move(point!.x, point!.y, { steps: 20 });
      await page.mouse.move(point!.x + 1, point!.y + 1);
      await page.mouse.up();
    };
    const node = (id: string) => page.locator(`.react-flow__node[data-id="${id}"]`);
    const idOf = async (type: string) => (await (await request.get('/api/nodes')).json()).filter((item: { type: string; name: string }) => item.type === type && !item.name.startsWith('Tutorial')).at(-1).id as string;
    await place('Text file', 530, 330);
    await at(page, 'move');
    const practice = node(await idOf('text'));
    await move(page, practice, 75, 40);
    await at(page, 'select');
    await practice.locator('.card-kind-icon').click({ modifiers: ['Shift'] });
    await at(page, 'open');
    await practice.locator('.card-kind-icon').click();
    await at(page, 'close');
    await practice.getByRole('button', { name: /^Close .* inspector$/ }).click();
    await at(page, 'focus');
    if (!await practice.evaluate(element => element.classList.contains('selected'))) await practice.click({ modifiers: ['Shift'], position: { x: 35, y: 25 } });
    await page.keyboard.press('f');
    await at(page, 'delete');
    await page.keyboard.press('Delete');
    await at(page, 'workflow');
    await page.getByRole('button', { name: 'Build my first workflow', exact: true }).click();
    await at(page, 'agent');
    await place('Agent', 350, 320);
    await at(page, 'model-settings');
    await page.getByRole('button', { name: 'Open settings', exact: true }).click();
    await at(page, 'model-connection');
    await page.getByRole('button', { name: 'Add connection', exact: true }).click();
    await at(page, 'model-credentials');
    await page.getByRole('button', { name: 'Next', exact: true }).click();
    await at(page, 'model-list');
    await page.getByRole('button', { name: 'Next', exact: true }).click();
    await at(page, 'model-save');
    await page.getByRole('button', { name: 'Set up later', exact: true }).click();
    await at(page, 'configure');
    const agentId = await idOf('agent'), agent = node(agentId);
    await agent.locator('.card-kind-icon').click();
    await agent.getByRole('textbox', { name: 'System instruction', exact: true }).fill('Help me plan a small garden.');
    await page.getByRole('region', { name: 'Tutorial guide' }).click({ position: { x: 10, y: 10 } });
    await page.getByRole('button', { name: 'Continue with these settings', exact: true }).click();
    await at(page, 'conversation');
    await place('Conversation', 800, 330);
    const conversationId = await idOf('conversation'), conversation = node(conversationId);
    await at(page, 'connect-demo');
    await page.getByRole('button', { name: 'Show the connection', exact: true }).click();
    await page.locator('.tutorial-next').filter({ hasText: 'Continue' }).click();
    await at(page, 'conversation-open');
    await expect(conversation.locator('.world-card')).toHaveAttribute('data-surface-level', 'workspace');
    await page.locator('.tutorial-next').filter({ hasText: 'Continue' }).click();
    await at(page, 'message');
    await conversation.locator('.workspace-composer textarea').fill('Help me plan a small garden.');
    await conversation.getByRole('button', { name: 'Send message', exact: true }).click();
    await at(page, 'reply');
    await page.screenshot({ path: 'test-results/onboarding-conversation.png' });
    await page.getByRole('button', { name: 'On to Sandbox', exact: true }).click();
    await at(page, 'sandbox');
    await place('Sandbox', 730, 370);
    const sandboxId = await idOf('sandbox'), sandbox = node(sandboxId);
    await at(page, 'sandbox-connect');
    await page.getByRole('button', { name: 'Recover this step' }).click();
    await expect(page.getByRole('button', { name: 'Recover this step' })).toBeEnabled();
    const sourcePort = (await agent.locator('[data-connection-side="bottom"]').boundingBox())!;
    const targetPort = (await sandbox.locator('[data-connection-side="top"]').boundingBox())!;
    const sourcePoint = { x: sourcePort.x + sourcePort.width / 2, y: sourcePort.y + sourcePort.height / 2 };
    expect(await page.evaluate(point => document.elementFromPoint(point.x, point.y)?.closest('.tutorial-bubble') !== null, sourcePoint)).toBe(false);
    await page.mouse.move(sourcePoint.x, sourcePoint.y); await page.mouse.down();
    await page.mouse.move(targetPort.x + targetPort.width / 2, targetPort.y + targetPort.height / 2, { steps: 15 }); await page.mouse.up();
    await expect(page.locator('mask rect[data-spotlight-target="agent"]')).toHaveAttribute('opacity', '1');
    await expect(page.locator('mask rect[data-spotlight-target="sandbox"]')).toHaveAttribute('opacity', '1');
    await expect(page.locator('mask rect[data-spotlight-target="capability-chooser"]')).toHaveAttribute('opacity', '1');
    await page.screenshot({ path: `test-results/tutorial-capability-chooser-${motion}.png` });
    await page.locator('input[name="relationship"][value="execute"]').check();
    await page.getByRole('button', { name: 'Grant capability', exact: true }).click();
    await at(page, 'sandbox-open');
    await expect(sandbox.locator('.world-card')).toHaveAttribute('data-surface-level', 'workspace');
    await page.locator('.tutorial-next').filter({ hasText: 'Continue' }).click();
    await at(page, 'sandbox-ready');
    await page.getByRole('button', { name: 'Try sticking cards', exact: true }).click();
    await at(page, 'glue-demo');
    await page.evaluate(() => {
      const samples: { x: number; y: number; viewport: string; glued: boolean }[] = [];
      Object.assign(window, { glueSamples: samples });
      const sample = () => {
        const card = [...document.querySelectorAll<HTMLElement>('.react-flow__node')].find(el => el.textContent?.includes('Tutorial \u00b7 stick with me'));
        if (card) {
          const box = card.getBoundingClientRect();
          samples.push({ x: box.x, y: box.y, glued: card.classList.contains('is-glued'), viewport: document.querySelector<HTMLElement>('.world-canvas .react-flow__viewport')!.style.transform });
        }
        if (document.querySelector('.tutorial-bubble')?.getAttribute('data-step') === 'glue-demo') requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    });
    await page.getByRole('button', { name: 'Show me sticking', exact: true }).click();
    await page.locator('.tutorial-next').filter({ hasText: 'Continue' }).click();
    await at(page, 'glue-reset');
    const samples = await page.evaluate(() => (window as unknown as { glueSamples: { x: number; y: number; viewport: string; glued: boolean }[] }).glueSamples);
    // During the approach, the second card must keep moving left until contact.
    // Compare only frames with the same camera and before the pair starts moving together.
    const approach = samples.filter(sample => !sample.glued && sample.viewport === samples.at(-1)?.viewport);
    if (motion === 'no-preference') {
      expect(approach.length).toBeGreaterThan(10);
      expect(approach[0].x - approach.at(-1)!.x).toBeGreaterThan(50);
      for (let i = 1; i < approach.length; i++) expect(approach[i].x - approach[i - 1].x).toBeLessThan(2);
    }
    const first = page.locator('.react-flow__node').filter({ has: page.getByText('Tutorial · stick me', { exact: true }) });
    const second = page.locator('.react-flow__node').filter({ has: page.getByText('Tutorial · stick with me', { exact: true }) });
    await expect(first).toHaveClass(/is-glued/);
    await page.screenshot({ path: 'test-results/onboarding-glue.png' });
    await page.getByRole('button', { name: 'My turn', exact: true }).click();
    await page.locator('.tutorial-next').filter({ hasText: 'Continue' }).click();
    await at(page, 'glue');
    await page.getByRole('button', { name: 'Glue', exact: true }).click();
    const a = (await first.boundingBox())!, b = (await second.boundingBox())!;
    await move(page, second, a.x + a.width - b.x, a.y - b.y);
    await at(page, 'glue-move');
    await move(page, first, 50, 35);
    await at(page, 'minister-card');
    await expect(page.locator('.tutorial-guide')).toHaveAttribute('data-moving', 'false');
    await page.screenshot({ path: `test-results/tutorial-minister-role-deck-${motion}.png` });
    await place('Minister role', 800, 330);
    const roleId = await idOf('core.minister-role'), role = node(roleId);
    await at(page, 'minister');
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Resume', exact: true }).click();
    await expect(page.locator('.tutorial-guide')).toHaveAttribute('data-moving', 'false');
    const roleIcon = (await role.locator('.card-kind-icon').boundingBox())!, agentBox = (await agent.boundingBox())!;
    await move(page, role, agentBox.x + agentBox.width / 2 - roleIcon.x - roleIcon.width / 2,
      agentBox.y + agentBox.height / 2 - roleIcon.y - roleIcon.height / 2);
    await expect(agent.locator(`[data-card-id="${agentId}"]`)).toHaveAttribute('data-minister', 'true');
    await expect(role).toHaveCount(0);
    await page.locator('.tutorial-next').filter({ hasText: 'Continue' }).click();
    await at(page, 'minister-presence');
    await page.getByRole('button', { name: /^Open Minister /  }).hover();
    await at(page, 'minister-message');
    await page.getByRole('button', { name: 'Try the model later', exact: true }).click();
    await at(page, 'minister-history');
    await agent.locator('.card-kind-icon').click();
    await agent.getByRole('tab', { name: 'Minister', exact: true }).click();
    await at(page, 'minister-safety');
    await expect.poll(async () => {
      const panel = await page.getByRole('region', { name: 'Minister permissions', exact: true }).boundingBox();
      const spotlight = await page.locator('.tutorial-spotlight').all();
      const boxes = await Promise.all(spotlight.map(item => item.boundingBox()));
      return !!panel && boxes.some(box => box && box.x <= panel.x && box.y <= panel.y && box.x + box.width >= panel.x + panel.width);
    }).toBe(true);
    await expect(page.locator('.tutorial-guide')).toHaveAttribute('data-moving', 'false');
    await page.screenshot({ path: 'test-results/onboarding-minister.png' });
    await page.getByRole('button', { name: 'Got it', exact: true }).click();
    await buildTutorialLegion(page, { agent: agentId, conversation: conversationId, sandbox: sandboxId });
    await page.getByRole('button', { name: 'Finish & keep my world', exact: true }).click();
    await expect(page.locator('.onboarding-layer')).toHaveCount(0);
    const remaining = await (await request.get('/api/nodes')).json();
    expect(remaining.filter((item: { name: string }) => item.name.startsWith('Tutorial'))).toHaveLength(0);
    expect(remaining.map((item: { id: string }) => item.id)).toEqual(expect.arrayContaining([agentId, conversationId, sandboxId]));
    expect((await (await request.get('/api/edges')).json()).map((edge: { relationship: string }) => edge.relationship)).toEqual(expect.arrayContaining(['participate', 'execute']));
    await page.reload();
    await expect(page.locator('.onboarding-layer')).toHaveCount(0);
    expect(errors).toEqual([]);
  });
});
