import { expect, test } from '@playwright/test';
import type { LibrarySnapshot } from '../src/state/cardLibrary';

test('connected group frames keep left tabs, gather into stacks and leave single cards unfolded', async ({ page, request }, info) => {
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }) },
  } });
  let state = await (await request.get('/api/card-library')).json() as LibrarySnapshot;
  const core = Object.values(state.packs).find(pack => pack.definition.cards.includes('text'))!;
  if (!core.opened) {
    const response = await request.post('/api/card-library/actions', { data: {
      action: 'open_pack', id: core.definition.id, expected_revision: state.revision,
    } });
    expect(response.ok()).toBe(true);
    state = await response.json();
  }
  const definition = state.card_definitions.text, entry = state.collection.text;
  state.collection = {};
  state.available_card_ids = [];
  state.preset_pack_ids = {};
  for (const [group, count] of [['First group', 6], ['Second group', 3], ['Single card', 1]] as const) {
    const ids = Array.from({ length: count }, (_, index) => `group-layout-${group}-${index}`);
    state.packs[group] = { ...core, definition: { ...core.definition, id: group, name: group, cards: ids }, opened: true };
    for (const [index, id] of ids.entries()) {
      state.card_definitions[id] = { ...definition, id, label: `${group} ${index + 1}`, user_creatable: true };
      state.collection[id] = { ...entry, card_id: id, source_pack_ids: [group] };
      state.available_card_ids.push(id);
    }
  }
  await page.route('**/api/card-library', route => route.fulfill({ json: state }));
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({ width: 820, height: 900 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  await library.getByRole('button', { name: /^Cards/ }).click();
  const board = library.locator('.library-group-board');
  const first = library.getByRole('group', { name: 'First group', exact: true });
  const single = library.getByRole('group', { name: 'Single card', exact: true });
  await expect(single.getByRole('button', { name: /^(Expand|Collapse) / })).toHaveCount(0);
  await expect(single.getByRole('button', { name: 'Inspect Single card 1' })).toBeVisible();
  const checkFrames = async () => {
    await expect.poll(() => board.evaluate(element => {
      const bounds = element.getBoundingClientRect();
      const items = [...element.querySelectorAll<HTMLElement>('[data-group-item]')];
      const paths = [...element.querySelectorAll<SVGGeometryElement>('.library-group-outlines path')];
      if (paths.length !== 3 || element.classList.contains('is-moving')) return false;
      const layout = element.querySelector<HTMLElement>('.library-group-layout')!;
      const styles = getComputedStyle(layout);
      const cardWidth = parseFloat(styles.getPropertyValue('--compact-card-width'));
      const rowGap = parseFloat(styles.getPropertyValue('--row-gap'));
      if (items.some(item => Math.abs(item.offsetWidth - cardWidth) > 1)) return false;
      if (items.some(item => item.offsetLeft < layout.offsetLeft || item.offsetLeft + item.offsetWidth > layout.offsetLeft + layout.clientWidth + 1
        || item.offsetTop + item.offsetHeight > layout.offsetTop + layout.offsetHeight + 1)) return false;
      let rowBottom = 0;
      for (const [index, item] of items.entries()) {
        const previous = items[index - 1];
        if (previous) {
          if (item.offsetLeft > previous.offsetLeft) {
            if (Math.abs(item.offsetTop - previous.offsetTop) > 1) return false;
          } else if (item.offsetTop - rowBottom < rowGap - 1) return false;
        }
        rowBottom = Math.max(rowBottom, item.offsetTop + item.offsetHeight);
      }
      return paths.every(path => {
        const group = [...element.querySelectorAll<HTMLElement>('.library-source-group')].find(node => node.dataset.source === path.dataset.source)!;
        const tab = group.querySelector('.library-group-label')!.getBoundingClientRect();
        const frame = path.getBBox();
        return Math.abs(tab.left - bounds.left - frame.x) < 1.5 && (path.getAttribute('d')!.match(/M /g) ?? []).length === 1
          && items.every(item => {
            const box = item.getBoundingClientRect();
            const point = new DOMPoint(box.left - bounds.left + box.width / 2, box.top - bounds.top + box.height / 2);
            return path.isPointInFill(point) === (item.dataset.source === path.dataset.source);
          });
      });
    })).toBe(true);
  };
  await checkFrames();
  await first.getByRole('button', { name: 'Collapse First group' }).click();
  await expect(first.locator('.library-group-stack')).toHaveCount(1);
  await checkFrames();
  await expect(first.locator('[data-library-card]')).toHaveCount(0);
  await first.getByRole('button', { name: 'View cards in First group' }).click();
  await checkFrames();
  await expect(first.locator('[data-library-card]')).toHaveCount(6);
  // Reversing the animation must leave no inert copies or missing real cards.
  await first.getByRole('button', { name: 'Collapse First group' }).dispatchEvent('click');
  await first.getByRole('button', { name: 'Expand First group' }).dispatchEvent('click');
  await checkFrames();
  await expect(board.locator('.library-group-ghost')).toHaveCount(0);
  await expect(first.locator('[data-library-card]')).toHaveCount(6);
  for (const width of [1280, 600, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await checkFrames();
    expect(await library.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath(`groups-${width}.png`), animations: 'disabled' });
  }
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await first.getByRole('button', { name: 'Collapse First group' }).focus();
  await page.keyboard.press('Enter');
  await expect(first.getByRole('button', { name: 'Expand First group' })).toBeFocused();
  await expect(board.locator('.library-group-ghost')).toHaveCount(0);
  await page.keyboard.press('Space');
  await checkFrames();
  await expect(first.locator('[data-library-card]')).toHaveCount(6);
  await first.getByRole('button', { name: 'Collapse First group' }).click();
  await library.getByLabel('Search cards', { exact: true }).fill('First group 2');
  await expect(library.getByRole('button', { name: 'Inspect First group 2' })).toBeVisible();
  await expect(library.getByRole('button', { name: /^(Expand|Collapse) First group$/ })).toHaveCount(0);
  await library.getByRole('button', { name: 'Inspect First group 2' }).click();
  await expect(library.getByRole('complementary', { name: 'Card details' }).getByRole('heading', { name: 'First group 2' })).toBeVisible();
  const selected = first.locator('.library-card.is-selected');
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await selected.scrollIntoViewIfNeeded();
    await expect.poll(() => selected.evaluate(card => {
      const face = card.querySelector('.library-card-stock')!.getBoundingClientRect();
      const tab = card.closest('.library-source-group')!.querySelector('.library-group-label')!.getBoundingClientRect();
      const left = Math.max(face.left, tab.left), right = Math.min(face.right, tab.right);
      const top = Math.max(face.top, tab.top), bottom = Math.min(face.bottom, tab.bottom);
      return right > left && bottom > top
        && card.contains(document.elementFromPoint((left + right) / 2, (top + bottom) / 2));
    })).toBe(true);
    await page.screenshot({ path: info.outputPath(`selected-above-tab-${width}.png`), animations: 'disabled' });
  }
  expect(errors).toEqual([]);
});
