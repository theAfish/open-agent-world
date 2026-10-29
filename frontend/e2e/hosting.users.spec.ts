import { expect, test, type Page } from '@playwright/test';

const origin = 'http://127.0.0.1:5184';
const password = 'test-hosted-password-93841';

async function login(page: Page, username: string) {
  await page.goto(origin);
  await page.getByLabel('Username', { exact: true }).fill(username);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
}

async function signOut(page: Page) {
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await page.waitForURL(`${origin}/auth/login`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByLabel('Username', { exact: true })).toBeVisible();
}

async function runtime(page: Page) {
  await page.bringToFront();
  await expect(page.locator('.legion-workspace')).toBeVisible();
  await expect(page.locator('.legion-window-titlebar')).toContainText('Customer workspace');
  await expect(page.getByLabel('Access password', { exact: true })).toHaveCount(0);
  await expect(page.locator('.world-canvas')).toHaveCount(0);
}

test('keeps the workspace and its draft mounted through network failures and reconnects without navigation', async ({ page, context }, testInfo) => {
  await page.clock.install();
  await login(page, 'alice');
  await runtime(page);
  await page.getByRole('tab', { name: 'Tasks', exact: true }).click();
  const draft = page.getByLabel('New task title');
  await draft.fill('Unsubmitted draft survives reconnect');
  const input = await draft.elementHandle();
  let failure: 'network' | 'server' | undefined = 'network';
  let checks = 0;
  await page.route('**/auth/me', route => {
    checks++;
    return failure === 'network' ? route.abort('internetdisconnected')
      : failure === 'server' ? route.fulfill({ status: 503, body: 'Temporarily unavailable' }) : route.continue();
  });
  let navigations = 0;
  page.on('framenavigated', frame => { if (frame === page.mainFrame()) navigations++; });
  await page.clock.fastForward(15_000);
  const notice = page.locator('.backend-unavailable');
  await expect(notice).toContainText('Reconnecting automatically');
  await expect(draft).toHaveValue('Unsubmitted draft survives reconnect');
  await expect(page.locator('#oaw-auth-shield')).toHaveCount(0);
  failure = 'server';
  await notice.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect.poll(() => checks).toBeGreaterThanOrEqual(2);
  await expect(notice).toBeVisible();
  await context.setOffline(true);
  await expect(notice).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('hosting-reconnecting.png'), fullPage: true });
  failure = undefined;
  await context.setOffline(false);
  await expect(notice).toHaveCount(0);
  expect(await input!.evaluate(element => element.isConnected)).toBe(true);
  await expect(draft).toHaveValue('Unsubmitted draft survives reconnect');
  expect(navigations).toBe(0);
  await page.screenshot({ path: testInfo.outputPath('hosting-recovered.png'), fullPage: true });
});

test('hosted accounts isolate runtime data, invalidate other tabs and share authentication with the canvas', async ({ page, context, browser }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const bobContext = await browser.newContext({ locale: 'en-US', viewport: { width: 1400, height: 900 } });
  const bob = await bobContext.newPage();
  bob.on('pageerror', error => errors.push(error.message));
  try {
    await login(page, 'alice');
    await runtime(page);
    await expect(page.locator('.hosted-account')).toContainText('Alice');
    for (const name of ['Edit layout', 'Back to canvas', 'Open settings', 'Publish application']) {
      await expect(page.getByRole('button', { name, exact: true })).toHaveCount(0);
    }
    await page.getByRole('tab', { name: 'Tasks', exact: true }).click();
    await page.getByLabel('New task title').fill('Alice private task');
    await page.getByRole('button', { name: 'Add task', exact: true }).click();
    await expect(page.locator('.task-row')).toContainText('Alice private task');
    await page.reload();
    await runtime(page);
    await page.getByRole('tab', { name: 'Tasks', exact: true }).click();
    await expect(page.locator('.task-row')).toContainText('Alice private task');
    const aliceIdentity = await (await page.request.get(`${origin}/auth/me`)).json();
    expect((await page.request.get(`${origin}/api/world`)).status()).toBe(404);

    await login(bob, 'bob');
    await runtime(bob);
    await expect(bob.locator('.hosted-account')).toContainText('Bob');
    await bob.getByRole('tab', { name: 'Tasks', exact: true }).click();
    await expect(bob.getByText('Alice private task', { exact: true })).toHaveCount(0);
    await bob.getByLabel('New task title').fill('Bob private task');
    await bob.getByRole('button', { name: 'Add task', exact: true }).click();
    await expect(bob.locator('.task-row')).toContainText('Bob private task');
    const bobIdentity = await (await bob.request.get(`${origin}/auth/me`)).json();
    expect(bobIdentity.instance.id).not.toBe(aliceIdentity.instance.id);
    const foreignWrite = await page.request.post(`${origin}/api/runtime-app/workspace/nodes`, {
      headers: { Origin: origin, 'X-OAW-Instance': bobIdentity.instance.id }, data: { type: 'text', name: 'cross-user-write' },
    });
    expect(foreignWrite.status()).toBe(409);
    expect((await foreignWrite.json()).error.code).toBe('instance_changed');
    await page.bringToFront();
    await expect(page.getByText('Bob private task', { exact: true })).toHaveCount(0);

    const otherTab = await context.newPage();
    await otherTab.goto(origin);
    await runtime(otherTab);
    await page.bringToFront();
    await signOut(page);
    await expect(otherTab.getByLabel('Username', { exact: true })).toBeVisible();
    await expect(otherTab.locator('.legion-workspace')).toHaveCount(0);
    await otherTab.close();

    // Reuse Alice's original browser tab to check account switching and browser persistence.
    await login(page, 'bob');
    await runtime(page);
    await page.getByRole('tab', { name: 'Tasks', exact: true }).click();
    await expect(page.locator('.task-row')).toContainText('Bob private task');
    await expect(page.getByText('Alice private task', { exact: true })).toHaveCount(0);
    const stored = await page.evaluate(() => Object.fromEntries(Object.keys(localStorage).map(key => [key, localStorage.getItem(key)])));
    expect(JSON.stringify(stored)).not.toMatch(/Alice|Bob|private task|test-hosted-password/);
    await signOut(page);

    await login(page, 'carol');
    await expect(page.locator('.world-canvas')).toBeVisible();
    const startBlank = page.getByRole('button', { name: 'Start blank', exact: true });
    // Carol is a fresh builder profile. The onboarding state arrives after
    // the canvas mounts, so an immediate isVisible() races that response.
    await expect(startBlank).toBeVisible();
    await startBlank.click();
    await expect(page.getByRole('region', { name: 'Welcome to Open Agent World' })).toBeHidden();
    await expect(page.locator('.hosted-account')).toContainText('Carol');
    await page.getByRole('button', { name: 'Open settings', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Storage', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Sandbox', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Close settings', exact: true }).click();
    await page.getByRole('button', { name: 'Open Pack and Card Library', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Install Pack from File...', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Store', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Close Library', exact: true }).click();
    await expect(page.locator('.card-library-modal')).not.toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('hosting-canvas.png'), fullPage: true });
    await signOut(page);
    await page.screenshot({ path: testInfo.outputPath('hosting-login.png'), fullPage: true });
    expect(errors).toEqual([]);
  } finally { await bobContext.close(); }
});
