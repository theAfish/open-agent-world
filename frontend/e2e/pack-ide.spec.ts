import { expect, test, type Locator, type Page } from '@playwright/test';
import { cp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const root = fileURLToPath(new URL('../../', import.meta.url));
const evidence = path.join(root, '.outputs/pack-ide-ux');
test.use({ actionTimeout: 15000 });

test.beforeEach(async ({ page, request }) => {
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-theme': 'dark', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }) } } });
  await page.setViewportSize({ width: 1440, height: 900 });
  await mkdir(evidence, { recursive: true });
});

async function openIDE(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click({ timeout: 90000 });
  await page.getByText('Developer tools', { exact: true }).click();
  await page.getByRole('button', { name: 'Pack IDE Experimental', exact: true }).click();
  const ide = page.getByRole('dialog', { name: 'Pack IDE', exact: true });
  await expect(ide).toBeVisible();
  return ide;
}
async function openSourceProject(ide: Locator, source: string) {
  await ide.getByRole('button', { name: 'Open project', exact: true }).click();
  await ide.getByLabel('Project path', { exact: true }).fill(source);
  await ide.getByRole('button', { name: 'Open folder', exact: true }).click();
  await expect(ide.getByText('EXPLORER', { exact: true })).toBeVisible();
}
const sourceEditor = (ide: Locator, file: string) => ide.getByRole('textbox', { name: `Source code: ${file}`, exact: true });
async function screenshot(page: Page, name: string) {
  // Card materials finish painting asynchronously after their content is mounted.
  await page.mouse.move(10, 10);
  await page.waitForTimeout(1200);
  await page.screenshot({ path: path.join(evidence, `${name}.png`) });
}
async function exportPack(page: Page, ide: Locator, destination: string) {
  await ide.getByRole('button', { name: 'Check', exact: true }).click();
  await expect(ide).toContainText('Check passed. Ready to build.');
  const downloaded = page.waitForEvent('download');
  await ide.getByRole('button', { name: 'Build / Export', exact: true }).click();
  const artifact = await downloaded;
  expect(await artifact.failure()).toBeNull();
  await artifact.saveAs(destination);
  await expect(ide).toContainText(artifact.suggestedFilename());
  return artifact;
}
async function closeIDE(ide: Locator) { await ide.getByRole('button', { name: 'Close Pack IDE', exact: true }).click(); }
async function installPack(page: Page, artifact: string) {
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  await library.getByLabel('Pack file', { exact: true }).setInputFiles(artifact);
  await library.getByRole('button', { name: 'Install Pack', exact: true }).click();
  return library;
}

test('Factory opens the experimental IDE over its existing packaging workspace', async ({ page, request }) => {
  const response = await request.post('/api/nodes', { data: { type: 'oaw.factory.pack', position: { x: 400, y: 200 } } });
  expect(response.status()).toBe(201);
  const card = await response.json();
  await page.goto('/');
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  await page.locator(`[data-card-id="${card.id}"]`).getByRole('button', { name: '打开编辑器', exact: true }).click();
  const workspace = page.locator(`[data-workspace-node-id="${card.id}"]`);
  await expect(page.locator(`[data-card-id="${card.id}"]`)).toHaveCSS('width', '1020px');
  // Match the Factory suite: let React Flow measure the expanded workspace before fitting it.
  await page.waitForTimeout(300);
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  await expect.poll(async () => (await workspace.boundingBox())!.width).toBeLessThan(1400);
  await expect(workspace.getByLabel('卡包名称', { exact: true })).toBeVisible();
  await workspace.getByLabel('卡包名称', { exact: true }).fill('Packaging draft');
  await workspace.getByText('Developer tools', { exact: true }).click();
  await workspace.getByRole('button', { name: 'Pack IDE Experimental', exact: true }).click();
  const ide = page.getByRole('dialog', { name: 'Pack IDE', exact: true });
  await expect(ide).toBeVisible();
  await expect(ide.getByText('Experimental', { exact: true })).toBeVisible();
  await expect(ide.getByLabel('Project name', { exact: true })).toBeEditable();
  await screenshot(page, 'factory-experimental-entry');
  await page.keyboard.press('Escape');
  await expect(ide).not.toBeVisible();
  await expect(workspace.getByLabel('卡包名称', { exact: true })).toHaveValue('Packaging draft');
  await expect(workspace).toBeVisible();
});

test('no-code author creates a card, edits its live surface, tests, builds and installs; disk conflicts preserve drafts', async ({ page, request }) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const ide = await openIDE(page);
  await ide.getByLabel('Project name', { exact: true }).fill('Studio Greeting');
  await screenshot(page, 'create');
  const creation = page.waitForResponse(response => response.url().endsWith('/api/packs/projects/create') && response.request().method() === 'POST');
  await ide.getByRole('button', { name: 'Create project', exact: true }).click();
  const project = await (await creation).json();
  await ide.locator('summary').filter({ hasText: '+ Add card' }).click();
  await ide.getByLabel('Card name', { exact: true }).fill('Greeting Card');
  const adding = page.waitForResponse(response => response.url().endsWith(`/api/packs/projects/${project.id}/cards`) && response.request().method() === 'POST');
  await ide.getByRole('button', { name: 'Create card', exact: true }).click();
  const added = await (await adding).json();
  const cardType = added.cards.at(-1).id;
  await ide.getByRole('button', { name: 'Select Title', exact: true }).click();
  await ide.getByRole('textbox', { name: 'Text', exact: true }).fill('Made in Pack IDE');
  await ide.getByLabel('X', { exact: true }).fill('36');
  await ide.getByRole('textbox', { name: 'Text', exact: true }).fill('Temporary title');
  await ide.getByRole('button', { name: 'Undo visual edit', exact: true }).click();
  await expect(ide.getByRole('textbox', { name: 'Text', exact: true })).toHaveValue('Made in Pack IDE');
  await ide.getByRole('button', { name: 'Add field', exact: true }).click();
  await ide.getByLabel('Field label', { exact: true }).fill('Audience');
  await ide.getByLabel('Field key', { exact: true }).fill('audience');
  await ide.getByLabel('Default value', { exact: true }).fill('Developers');
  await ide.getByRole('button', { name: 'Select 运行', exact: true }).click();
  await ide.getByRole('button', { name: 'Remove element', exact: true }).click();
  await ide.getByRole('button', { name: 'Add button', exact: true }).click();
  await ide.getByRole('textbox', { name: 'Text', exact: true }).fill('Greet');
  await ide.getByLabel('X', { exact: true }).fill('28');
  await ide.getByLabel('Y', { exact: true }).fill('402');
  await ide.getByLabel('Width', { exact: true }).fill('372');
  await screenshot(page, 'design');
  await ide.getByRole('button', { name: 'Function', exact: true }).click();
  await ide.getByRole('button', { name: 'Select Greet', exact: true }).click();
  await expect(ide.getByRole('combobox', { name: 'Bound action', exact: true })).toHaveValue('run');
  await ide.getByRole('combobox', { name: 'Operation', exact: true }).selectOption('template');
  await ide.getByRole('textbox', { name: /^Output template/ }).fill('Hello {{audience}} from Schema 3');
  await ide.getByRole('button', { name: 'Test action', exact: true }).click();
  await expect(ide.getByLabel('Action test result', { exact: true })).toContainText('Hello Developers from Schema 3');
  await screenshot(page, 'function');
  await ide.getByRole('button', { name: 'Preview', exact: true }).click();
  await ide.locator('.pack-ide-preview').getByLabel('Audience', { exact: true }).fill('Visual author');
  await ide.getByRole('button', { name: 'Greet', exact: true }).click();
  await expect(ide.locator('.pack-ide-preview output')).toContainText('Hello Visual author from Schema 3');
  await ide.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(ide).toContainText('Project files saved.');
  const artifactPath = path.join(evidence, 'visual-greeting.oawpack');
  await exportPack(page, ide, artifactPath);
  await screenshot(page, 'build');
  await ide.getByRole('button', { name: 'Close bottom panel', exact: true }).click();

  // Real external edit while a keyboard-authored CodeMirror draft is dirty.
  await ide.getByRole('button', { name: 'README.md', exact: true }).click();
  const editor = sourceEditor(ide, 'README.md');
  await editor.press('Control+End');
  await page.keyboard.insertText('\nUnsaved editor draft\n');
  const diskPath = path.join(project.path, 'README.md');
  const original = await readFile(diskPath, 'utf8');
  await writeFile(diskPath, original + '\nExternal editor change\n');
  await ide.getByRole('button', { name: 'Refresh project', exact: true }).click();
  await expect(ide.getByText('Files changed externally', { exact: true })).toBeVisible();
  await expect(editor).toContainText('Unsaved editor draft');
  await expect(ide.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
  await ide.locator('summary').filter({ hasText: 'Compare with disk' }).click();
  await ide.getByRole('button', { name: 'Compare README.md', exact: true }).click();
  await expect(ide.getByLabel('Disk README.md')).toContainText('External editor change');
  await expect(ide.getByLabel('Draft README.md')).toContainText('Unsaved editor draft');
  expect(await readFile(diskPath, 'utf8')).not.toContain('Unsaved editor draft');
  await screenshot(page, 'external-conflict');
  page.once('dialog', dialog => dialog.accept());
  await ide.getByRole('button', { name: 'Reload from disk', exact: true }).click();
  await closeIDE(ide);
  const library = await installPack(page, artifactPath);
  await expect.poll(async () => (await (await request.get('/api/catalog')).json()).node_types.some((node: { id: string }) => node.id === cardType)).toBe(true);
  await library.getByRole('button', { name: 'Close Library', exact: true }).click();
  const created = await request.post('/api/nodes', { data: { type: cardType, position: { x: 400, y: 200 } } });
  expect(created.status()).toBe(201);
  const card = await created.json();
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  await page.locator(`[data-card-id="${card.id}"]`).getByText('Made in Pack IDE', { exact: true }).click();
  const runtime = page.locator(`[data-workspace-node-id="${card.id}"]`);
  await runtime.getByLabel('Audience', { exact: true }).fill('Installed runtime');
  await runtime.getByRole('button', { name: '运行', exact: true }).click();
  await expect(runtime.locator('output')).toContainText('Hello Installed runtime from Schema 3');
  await screenshot(page, 'visual-installed-runtime');
  expect(errors).toEqual([]);
});

test('CodeMirror edits real Python, preserves context, exports the changed wheel and runs its installed action', async ({ page, request }, testInfo) => {
  test.setTimeout(150000);
  const source = path.join(root, '.tmp', `pack-ide-ux-analysis-${Date.now()}`);
  await cp(path.join(root, 'examples/packs/analysis-card'), source, { recursive: true });
  const pythonPath = 'src/oaw_example_analysis_card/__init__.py';
  const original = (await readFile(path.join(source, pythonPath), 'utf8')).replace(/\r\n/g, '\n');
  const ide = await openIDE(page);
  await openSourceProject(ide, source);
  await ide.getByRole('button', { name: pythonPath, exact: true }).click();
  const editor = sourceEditor(ide, pythonPath);
  await expect(editor).toContainText('dimensions.a * dimensions.b * dimensions.c');
  await expect(ide.locator('.cm-gutters')).toBeVisible();
  await editor.press('Control+Home');
  await editor.press('Control+f');
  const find = ide.getByPlaceholder('Find', { exact: true });
  await find.pressSequentially('volume = dimensions.a * dimensions.b * dimensions.c');
  await find.press('Enter');
  await find.press('Escape');
  await editor.press('End');
  await expect(ide.locator('.pack-source-editor-status')).toContainText('Ln 24,');
  const cursor = await ide.locator('.pack-source-editor-status').getByText(/^Ln /).textContent();
  // File tabs and mode unmounts must retain the actual editor selection.
  await ide.getByRole('button', { name: 'pyproject.toml', exact: true }).click();
  await ide.getByRole('tab', { name: '__init__.py', exact: true }).click();
  await expect(ide.locator('.pack-source-editor-status')).toContainText(cursor!);
  await ide.getByRole('button', { name: 'Design', exact: true }).click();
  await ide.getByRole('button', { name: 'Select Title', exact: true }).click();
  await ide.getByRole('textbox', { name: 'Text', exact: true }).fill('Edited Python analyzer');
  const fieldLayout = await ide.locator('.pack-visual-fields').evaluate(element => ({
    clientHeight: element.clientHeight, scrollHeight: element.scrollHeight, rows: [...element.children].map(child => {
      const rect = child.getBoundingClientRect(); return { top: rect.top, bottom: rect.bottom };
    }), gridAutoRows: getComputedStyle(element).gridAutoRows,
  }));
  expect(fieldLayout.scrollHeight).toBeGreaterThan(fieldLayout.clientHeight);
  expect(fieldLayout.rows.every((row, index) => index === 0 || row.top >= fieldLayout.rows[index - 1].bottom)).toBe(true);
  await writeFile(path.join(evidence, 'field-layout.json'), JSON.stringify(fieldLayout, null, 2));
  // Give all three scientific inputs room, using the real contextual layout editor.
  await ide.getByRole('button', { name: 'Select Cell a (A)', exact: true }).click();
  await ide.getByLabel('Y', { exact: true }).fill('244');
  await ide.getByLabel('Height', { exact: true }).fill('192');
  await ide.getByRole('button', { name: 'Select Analyze structure', exact: true }).click();
  await ide.getByLabel('Y', { exact: true }).fill('448');
  await ide.getByRole('button', { name: 'Select Result', exact: true }).click();
  await ide.getByLabel('Y', { exact: true }).fill('500');
  await ide.getByLabel('Height', { exact: true }).fill('54');
  await ide.getByRole('button', { name: 'Select Title', exact: true }).click();
  await screenshot(page, 'mixed-design');
  await ide.getByRole('button', { name: 'Function', exact: true }).click();
  await expect(ide).toContainText('analyze_structure');
  await screenshot(page, 'mixed-function');
  await ide.getByRole('button', { name: 'Code', exact: true }).click();
  await expect(ide.locator('.pack-source-editor-status')).toContainText(cursor!);
  await editor.focus();
  await page.keyboard.insertText(' * 2');
  await expect(editor).toContainText('volume = dimensions.a * dimensions.b * dimensions.c * 2');
  await editor.press('Control+z');
  await expect(editor).not.toContainText('dimensions.c * 2');
  await editor.press('Control+y');
  await expect(editor).toContainText('dimensions.c * 2');
  await screenshot(page, 'code');
  await page.setViewportSize({ width: 1366, height: 768 });
  await screenshot(page, 'code-1366');
  await page.setViewportSize({ width: 1440, height: 900 });
  await editor.press('Control+s');
  await expect(ide.getByRole('button', { name: 'Check', exact: true })).toBeEnabled();
  const edited = await readFile(path.join(source, pythonPath), 'utf8');
  expect(edited).toBe(original.replace('volume = dimensions.a * dimensions.b * dimensions.c', 'volume = dimensions.a * dimensions.b * dimensions.c * 2'));
  expect(JSON.parse(await readFile(path.join(source, 'designs/card.json'), 'utf8')).face.title).toBe('Edited Python analyzer');

  // A source error must be visible and navigable, then normal undo repairs it.
  await editor.press('Control+Home');
  await page.keyboard.insertText('def broken(:\n');
  await editor.press('Control+s');
  await expect(ide.getByRole('button', { name: 'Check', exact: true })).toBeEnabled();
  await ide.getByRole('button', { name: 'Check', exact: true }).click();
  const diagnostics = ide.getByRole('region', { name: 'Diagnostics', exact: true });
  await diagnostics.getByRole('button', { name: /invalid syntax.*__init__\.py:1/ }).click();
  await expect(ide.locator('.pack-source-editor-status')).toContainText('Ln 1, Col 12');
  await screenshot(page, 'source-diagnostic');
  await editor.press('Control+z');
  await editor.press('Control+s');
  await expect(ide.getByRole('button', { name: 'Check', exact: true })).toBeEnabled();
  expect(await readFile(path.join(source, pythonPath), 'utf8')).toBe(edited);
  await ide.getByRole('button', { name: 'Close bottom panel', exact: true }).click();

  // A real compiler runs in the external author toolchain, never in the host API.
  execFileSync('uv', ['build', '--wheel', '--offline', '--cache-dir', path.join(root, '.tmp/uv-cache'), '--out-dir', 'dist'],
    { cwd: source, windowsHide: true, timeout: 60000 });
  await ide.getByRole('button', { name: 'Refresh project', exact: true }).click();
  const artifactPath = path.join(evidence, 'edited-analysis.oawpack');
  await exportPack(page, ide, artifactPath);
  await closeIDE(ide);
  await installPack(page, artifactPath);
  await expect.poll(async () => JSON.stringify(await (await request.get('/api/packs', { headers: { 'x-oaw-pack-install': '1' } })).json())).toContain('example.analysis-card');

  // The archive is activated in a fresh disposable process through the ordinary
  // resource endpoint, not the IDE's bounded visual evaluator.
  const runtime = execFileSync(path.join(root, 'backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'), ['-c', `
import json, sys
from pathlib import Path
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app
from backend.packs.installation import PackInstallationManager
from backend.plugins.loader import create_builtin_registry, load_installed_packs
from backend.services import create_services
settings = Settings.for_data_root(Path(sys.argv[2]))
registry = create_builtin_registry()
PackInstallationManager(settings.data_root, registry).install(Path(sys.argv[1]).read_bytes())
load_installed_packs(registry, settings.data_root, strict=True)
definition = registry.node_type('example.analysis-card')
assert definition.authoring['face']['title'] == 'Edited Python analyzer'
with TestClient(create_app(settings, services=create_services(settings, plugins=registry)), client=('127.0.0.1', 50000)) as client:
    card = client.post('/api/nodes', json={'type': 'example.analysis-card'})
    assert card.status_code == 201, card.text
    url = '/api/nodes/' + card.json()['id'] + '/resource/analyze_structure'
    result = client.post(url, json={'arguments': {'a': 3, 'b': 5, 'c': 7}})
    assert result.status_code == 200, result.text
    assert result.json()['volume'] == 210, result.text
    invalid = client.post(url, json={'arguments': {'a': -1, 'b': 5, 'c': 7}})
    assert invalid.status_code == 422, invalid.text
    print(json.dumps(result.json()))
`, artifactPath, testInfo.outputPath('runtime-profile')], { cwd: root, windowsHide: true, encoding: 'utf8', timeout: 60000 });
  expect(runtime).toContain('Orthogonal cell volume: 210 A^3');
  await writeFile(path.join(evidence, 'mixed-receipt.json'), JSON.stringify({ source, pythonPath, artifactPath,
    sourceHash: createHash('sha256').update(edited).digest('hex'), runtime: JSON.parse(runtime),
    profile: process.env.OAW_E2E_DATA_ROOT }, null, 2));
});

test('custom TSX stays editable and its installed component retains real behavior', async ({ page, request }) => {
  test.setTimeout(120000);
  const source = path.join(root, '.tmp', `pack-ide-ux-notes-${Date.now()}`);
  await cp(path.join(root, 'examples/packs/notes'), source, { recursive: true });
  const sourcePath = path.join(source, 'frontend/index.tsx');
  const original = await readFile(sourcePath);
  execFileSync(process.execPath, ['--input-type=module', '-e',
    `import {buildPackFrontend} from ${JSON.stringify(new URL('../pack-sdk/build.mjs', import.meta.url).href)}; await buildPackFrontend(${JSON.stringify({ entryPoint: sourcePath, outfile: path.join(source, 'dist/frontend/index.js') })});`],
  { cwd: root, windowsHide: true });
  const ide = await openIDE(page);
  await openSourceProject(ide, source);
  await ide.getByRole('button', { name: 'frontend/index.tsx', exact: true }).click();
  await expect(sourceEditor(ide, 'frontend/index.tsx')).toContainText('data-note-output');
  await expect(ide.locator('.pack-source-editor')).toHaveAttribute('data-language', 'TSX');
  const artifactPath = path.join(evidence, 'notes.oawpack');
  await exportPack(page, ide, artifactPath);
  await closeIDE(ide);
  await installPack(page, artifactPath);
  await expect.poll(async () => (await (await request.get('/api/catalog')).json()).node_types.some(
    (node: { id: string }) => node.id === 'example.notes')).toBe(true);
  expect((await request.post('/api/nodes', { data: { type: 'example.notes', config: { message: 'Custom frontend retained' } } })).status()).toBe(201);
  await page.getByRole('button', { name: 'Pack IDE Experimental', exact: true }).click();
  await openSourceProject(ide, source);
  await ide.getByRole('button', { name: 'Preview', exact: true }).click();
  await ide.getByLabel('Use installed card', { exact: true }).check();
  await expect(ide.locator('[data-note-output]')).toHaveText('Custom frontend retained');
  await ide.getByLabel('Note message', { exact: true }).fill('Edited through installed preview');
  await ide.getByRole('button', { name: 'Save note', exact: true }).click();
  await expect(ide.locator('[data-note-output]')).toHaveText('Edited through installed preview');
  await screenshot(page, 'custom-runtime-preview');
  expect(await readFile(sourcePath)).toEqual(original);
});
