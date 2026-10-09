import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { TEST_CATALOG } from '../src/state/catalog.fixture';

test('Image cards accept screenshot paste through the shared intake (real HTTP)', async ({ page, request }) => {
  const card = await (await request.post('/api/nodes', { data: { type: 'image', name: 'Pasted screenshot', position: { x: 550, y: 340 } } })).json();
  try {
    await page.goto('/');
    const surface = page.locator(`[data-card-id="${card.id}"]`);
    await expect(surface).toBeVisible();
    if (await surface.getAttribute('data-surface-level') !== 'inspector') await surface.locator('.card-eyebrow').click();
    const target = surface.getByRole('button', { name: 'Import image', exact: true });
    await expect(target).toBeVisible();
    await target.focus();
    await target.evaluate(element => {
      const bytes = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1cAAAAASUVORK5CYII='), character => character.charCodeAt(0));
      const clipboardData = new DataTransfer();
      clipboardData.items.add(new File([bytes], 'screenshot.png', { type: 'image/png' }));
      element.dispatchEvent(new ClipboardEvent('paste', { clipboardData, bubbles: true, cancelable: true }));
    });
    await expect(surface.locator('.image-preview-expanded img')).toHaveJSProperty('naturalWidth', 1);
    await expect(surface.getByRole('button', { name: 'Import image', exact: true })).toHaveCount(0);
    await expect(page.getByText('Text could not be loaded', { exact: true })).toHaveCount(0);
    await page.screenshot({ path: '../.outputs/ux-image-paste.png' });
  } finally { await request.delete(`/api/nodes/${card.id}`); }
});

test('Sandbox drop targets preserve directories, refresh and offer context downloads (mock runtime API)', async ({ page }) => {
  const files = new Map<string, string | null>([['inputs', null], ['existing.txt', 'original content']]);
  const card = { id: 'file-intake', type: 'sandbox', name: 'File workspace', position: { x: 550, y: 340 },
    size: { width: 96, height: 96 }, expanded: false, status: 'ready', config: { runtime: 'windows', workspace_access: 'read_write' } };
  await page.routeWebSocket('**/ws/events', () => {});
  await page.route(/^https?:\/\/[^/]+\/api\//, async route => {
    const url = new URL(route.request().url()), path = url.pathname;
    const reply = (json: unknown, status = 200) => route.fulfill({ json, status });
    if (path.startsWith('/api/application') || ['/api/deployment', '/api/card-library', '/api/settings/models', '/api/canvas/glue'].includes(path)) return route.continue();
    if (path === '/api/catalog') return reply(TEST_CATALOG);
    if (path === '/api/world') return reply({ nodes: [card], edges: [], chunks: ['0:0'] });
    if (path === '/api/legions' || path === '/api/legions/presets' || path.endsWith('/history')) return reply([]);
    if (path === '/api/sandbox/runtimes') return reply({ default_runtime: 'windows', runtimes: [] });
    if (path.endsWith('/files')) {
      const relative = url.searchParams.get('path') ?? '';
      if (route.request().method() === 'DELETE') {
        for (const name of [...files.keys()]) if (name === relative || name.startsWith(relative + '/')) files.delete(name);
        return reply({ deleted: relative });
      }
      if (route.request().method() === 'POST') {
        const directory = url.searchParams.get('directory') === 'true';
        if (files.has(relative) && !directory) return reply({ detail: 'Destination already exists. Choose another filename.' }, 422);
        files.set(relative, directory ? null : route.request().postData() ?? '');
        return reply(directory ? { created: true } : { written: files.get(relative)!.length }, 201);
      }
      const operation = url.searchParams.get('operation');
      if (operation === 'list') {
        const prefix = relative ? relative + '/' : '';
        const query = url.searchParams.get('query') ?? '', cursor = Number(url.searchParams.get('cursor') ?? 0);
        const entries = [...files].filter(([name]) => query ? name.includes(query) : name.startsWith(prefix) && !name.slice(prefix.length).includes('/') && name !== relative)
          .map(([name, content]) => ({ name: name.split('/').at(-1)!, path: name, directory: content === null, blocked: false, size: content?.length ?? 0 }));
        return reply({ entries: entries.slice(cursor, cursor + 30), next_cursor: entries.length > cursor + 30 ? String(cursor + 30) : undefined });
      }
      if (operation === 'preview') return reply({ state: 'text', text: files.get(relative) });
      if (operation === 'download') return route.fulfill({ contentType: 'application/octet-stream', body: files.get(relative) ?? '' });
      return reply([{ id: 'workspace', label: 'Workspace', access: 'read_write', directory: true }]);
    }
    if (path.endsWith('/files/move')) {
      const { path: source, destination } = route.request().postDataJSON();
      if (files.has(destination)) return reply({ detail: 'Destination already exists' }, 422);
      for (const [name, value] of [...files]) if (name === source || name.startsWith(source + '/')) {
        files.delete(name); files.set(destination + name.slice(source.length), value);
      }
      return reply({ moved: source, destination });
    }
    if (path.endsWith('/configuration')) return reply({ ready: true, variables: [] });
    if (path.endsWith(card.id)) return reply({ sandbox_id: card.id, state: 'ready', available: true, platform: 'windows', runtime_id: 'windows',
      workspace: 'C:\\workspace', workspace_path: null, workspace_access: 'read_write', runtime_locked: true, shell: ['cmd.exe'] });
    return reply({ detail: path }, 404);
  });
  await page.goto('/');
  const panel = page.getByLabel('Sandbox files', { exact: true });
  const folder = panel.getByRole('button', { name: 'inputs', exact: true });
  await expect(folder).toBeVisible();
  await expect(panel.getByRole('searchbox')).toHaveCount(0);
  await page.screenshot({ path: '../.outputs/ux-files-collapsed.png' });
  await folder.evaluate(element => {
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(new File(['nested upload'], 'notes.txt', { type: 'text/plain' }));
    element.dispatchEvent(new DragEvent('dragenter', { bubbles: true, dataTransfer }));
    element.dispatchEvent(new DragEvent('dragover', { bubbles: true, dataTransfer }));
  });
  await expect(folder.locator('..')).toHaveAttribute('data-drop-target', 'true');
  await expect(folder).toHaveAttribute('aria-expanded', 'true');
  await page.screenshot({ path: '../.outputs/file-intake-sandbox-drop.png' });
  await folder.evaluate(element => {
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(new File(['nested upload'], 'notes.txt', { type: 'text/plain' }));
    element.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer }));
  });
  await expect(panel.getByRole('button', { name: '1 completed', exact: true })).toBeVisible();
  expect(files.get('inputs/notes.txt')).toBe('nested upload');
  const note = panel.getByRole('button', { name: 'notes.txt', exact: true });
  await expect(note).toBeVisible();
  await note.click();
  await expect(page.locator('.sandbox-preview pre')).toHaveText('nested upload');
  await note.click({ button: 'right' });
  const menu = page.getByRole('menu', { name: 'File actions' });
  await expect(menu).toBeVisible();
  await page.screenshot({ path: '../.outputs/file-intake-sandbox-menu.png' });
  await menu.getByRole('menuitem', { name: 'Preview', exact: true }).click();
  await expect(page.locator('.sandbox-preview pre')).toHaveText('nested upload');
  await note.focus();
  await page.keyboard.press('Shift+F10');
  const download = page.waitForEvent('download');
  await menu.getByRole('menuitem', { name: 'Download', exact: true }).click();
  const result = await download;
  expect(result.suggestedFilename()).toBe('notes.txt');
  expect(await readFile((await result.path())!, 'utf8')).toBe('nested upload');
  // Use native browser dragging for existing entries, not the upload mock.
  const root = panel.getByRole('button', { name: 'Workspace', exact: true });
  await note.dragTo(root);
  await expect.poll(() => files.get('notes.txt')).toBe('nested upload');
  await expect(page.locator('.sandbox-preview-path')).toContainText('notes.txt');
  expect(files.has('inputs/notes.txt')).toBe(false);
  await note.dragTo(folder);
  await expect.poll(() => files.get('inputs/notes.txt')).toBe('nested upload');
  expect(files.has('notes.txt')).toBe(false);
  await note.click({ button: 'right' });
  await menu.getByRole('menuitem', { name: 'Delete', exact: true }).click();
  let confirmation = page.getByRole('dialog', { name: 'Delete notes.txt?' });
  await confirmation.getByRole('button', { name: 'Cancel', exact: true }).click();
  expect(files.has('inputs/notes.txt')).toBe(true);
  await note.click({ button: 'right' });
  await menu.getByRole('menuitem', { name: 'Delete', exact: true }).click();
  await page.screenshot({ path: '../.outputs/ux-file-delete.png' });
  await confirmation.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(note).toHaveCount(0);
  await expect(page.locator('.sandbox-preview pre')).toHaveCount(0);

  await panel.evaluate(element => {
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(new File(['value'], 'result.txt'));
    const file = { name: 'result.txt', isFile: true, isDirectory: false, file: (done: (file: File) => void) => done(new File(['value'], 'result.txt')) };
    const directory = (name: string, children: unknown[]) => ({ name, isFile: false, isDirectory: true, createReader: () => {
      let read = false;
      return { readEntries: (done: (entries: unknown[]) => void) => { done(read ? [] : children); read = true; } };
    } });
    const drop = new DragEvent('drop', { bubbles: true, cancelable: true });
    // Browser-created File objects do not have OS directory entries. Supply the
    // directory reader contract explicitly, including the empty-directory case.
    Object.defineProperty(drop, 'dataTransfer', { value: { types: ['Files'], files: [], items: [{ kind: 'file',
      getAsFile: () => null, webkitGetAsEntry: () => directory('project', [directory('empty', []), directory('nested', [file])]),
    }] } });
    element.dispatchEvent(drop);
  });
  await expect(panel.getByRole('button', { name: '6 completed', exact: true })).toBeVisible();
  expect(files.get('project/empty')).toBeNull();
  expect(files.get('project/nested/result.txt')).toBe('value');
  await panel.locator('input[type=file][aria-label="Upload files"]').setInputFiles({ name: 'existing.txt', mimeType: 'text/plain', buffer: Buffer.from('replacement') });
  await expect(panel.getByRole('alert')).toContainText('existing.txt');
  expect(files.get('existing.txt')).toBe('original content');
  for (let i = 0; i < 35; i++) files.set(`extra-${i}.txt`, String(i));
  await panel.getByRole('button', { name: 'Refresh files' }).click();
  await panel.getByRole('button', { name: 'Load more', exact: true }).click();
  await expect(panel.getByRole('button', { name: 'extra-34.txt', exact: true })).toBeVisible();
  await expect(panel.locator('.sandbox-file-row input[type=checkbox]')).toHaveCount(0);
  await panel.getByRole('button', { name: 'existing.txt', exact: true }).click({ modifiers: ['ControlOrMeta'] });
  await panel.getByRole('button', { name: 'extra-34.txt', exact: true }).click({ modifiers: ['ControlOrMeta'] });
  await expect(panel.getByText('2 selected', { exact: true })).toBeVisible();
  await expect(panel.getByRole('button', { name: 'existing.txt', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(panel.getByRole('button', { name: 'extra-34.txt', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.screenshot({ path: '../.outputs/ux-file-multiselect.png' });
  await expect(panel.getByRole('button', { name: 'Download selected files' })).toBeEnabled();
  const searchToggle = panel.getByRole('button', { name: 'Search workspace files' });
  const searchInput = panel.getByRole('searchbox', { name: 'Search workspace files' });
  await searchToggle.click();
  await expect(searchToggle).toHaveAttribute('aria-expanded', 'true');
  await expect(searchInput).toBeFocused();
  await searchInput.fill('result');
  await expect(panel.getByRole('button', { name: 'project/nested/result.txt', exact: true })).toBeVisible();
  await expect(panel.getByText('2 selected', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: '../.outputs/ux-files-search.png' });
  await searchInput.press('Escape');
  await expect(searchInput).toHaveCount(0);
  await expect(searchToggle).toBeFocused();
  await expect(searchToggle).toHaveAttribute('aria-expanded', 'false');
  await expect(panel.getByRole('button', { name: 'extra-34.txt', exact: true })).toBeVisible();
  await searchToggle.click();
  await expect(searchInput).toHaveValue('');
  await searchInput.fill('result');
  await panel.getByRole('button', { name: 'Clear search' }).click();
  await expect(searchInput).toHaveValue('');
  await expect(searchInput).toBeFocused();
  await searchToggle.click();
  await expect(searchInput).toHaveCount(0);
  // Move an entire folder, then remove it with its nested contents.
  const project = panel.getByRole('button', { name: 'project', exact: true });
  await project.dragTo(folder);
  await expect.poll(() => files.get('inputs/project/nested/result.txt')).toBe('value');
  await project.click({ button: 'right' });
  await menu.getByRole('menuitem', { name: 'Delete', exact: true }).click();
  confirmation = page.getByRole('dialog', { name: 'Delete project?' });
  await expect(confirmation).toContainText('This folder and its contents will be permanently deleted.');
  await confirmation.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(project).toHaveCount(0);
  expect([...files.keys()].some(name => name.startsWith('inputs/project'))).toBe(false);
});

test('Conversation drop uploads real attachments and persists them after sending and reloading', async ({ page, request }) => {
  const room = await (await request.post('/api/nodes', { data: { type: 'conversation', name: 'Drop attachments', position: { x: 550, y: 340 } } })).json();
  try {
    await page.goto('/');
    const workspace = page.locator(`[data-workspace-node-id="${room.id}"]`);
    const input = workspace.getByLabel('Conversation message', { exact: true });
    await expect(input).toBeEditable();
    await input.evaluate(element => {
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(new File(['name,value\nx,42\n'], 'data.csv', { type: 'text/csv' }));
      element.dispatchEvent(new DragEvent('dragenter', { bubbles: true, dataTransfer }));
    });
    await expect(workspace.getByText('Drop files to attach to this conversation')).toBeVisible();
    await page.screenshot({ path: '../.outputs/file-intake-conversation-drop.png' });
    await input.evaluate(element => {
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(new File(['name,value\nx,42\n'], 'data.csv', { type: 'text/csv' }));
      element.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer }));
    });
    await expect(workspace.getByRole('button', { name: 'Remove attachment data.csv' })).toBeVisible();
    await expect(workspace.getByRole('button', { name: 'Remove attachment data.csv' })).toHaveCSS('border-top-color', 'rgba(0, 0, 0, 0)');
    await workspace.locator('.workspace-composer').screenshot({ path: '../.outputs/ux-pending-attachment.png' });
    await workspace.getByRole('button', { name: 'Send message', exact: true }).click();
    await expect(workspace.getByRole('link', { name: 'Download data.csv' })).toBeVisible();
    await workspace.getByRole('button', { name: 'Open data.csv' }).click();
    const preview = page.getByRole('dialog', { name: 'Preview data.csv' });
    await expect(preview.locator('pre')).toHaveText('name,value\nx,42');
    await page.screenshot({ path: '../.outputs/ux-attachment-preview.png' });
    await preview.getByRole('button', { name: 'Close', exact: true }).click();
    await page.reload();
    const download = page.waitForEvent('download');
    await workspace.getByRole('link', { name: 'Download data.csv' }).click();
    const result = await download;
    expect(await readFile((await result.path())!, 'utf8')).toBe('name,value\nx,42\n');
  } finally { await request.delete(`/api/nodes/${room.id}`); }
});
