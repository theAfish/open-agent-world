import { describe, expect, it } from 'vitest';
import {
  activateTab, activePaneView, dockPane, dropSide, layoutMinimum, paneViews, readWorkspaceLayout,
  removePane, resizeSplit, retainPanes, retainWorkspaceLayout, stackPane, viewKey,
  type WorkspaceLeaf, type WorkspaceNode, type WorkspaceView,
} from './workspaceLayout';

const view = (card_id: string, section_id?: string): WorkspaceView => section_id ? { card_id, section_id } : { card_id };
const pane = (card_id: string, section_id?: string): WorkspaceNode => ({ kind: 'pane', view: view(card_id, section_id) });
const tabs = (views: WorkspaceView[], active_view = views[0]): WorkspaceLeaf => ({ kind: 'tabs', views, active_view });
const split = (first: WorkspaceNode, second: WorkspaceNode): WorkspaceNode => ({ kind: 'split', axis: 'horizontal', ratio: .5, first, second });
const ids = (root: WorkspaceNode | null) => paneViews(root).map(item => item.card_id);

const a = view('a'), b = view('b'), c = view('c');

describe('Legion workspace layout', () => {
  it('stacks views, reorders tabs and moves them across regions without duplication', () => {
    let root = stackPane(pane('a'), b, a);
    root = stackPane(root, c, a, b);
    expect(root).toEqual(tabs([a, c, b], c));
    root = stackPane(root, b, c, a);
    expect(ids(root)).toEqual(['b', 'a', 'c']);
    root = dockPane(root, b, b, 'right');
    expect(root).toMatchObject({ first: tabs([a, c], a), second: pane('b') });
    root = stackPane(root, c, b);
    expect(root).toMatchObject({ first: pane('a'), second: tabs([b, c], c) });
    expect(activateTab(root!, b)).toMatchObject({ second: { active_view: b } });
    expect(stackPane(root, a, view('missing'))).toBe(root);
    expect(stackPane(root, c, c, c)).toBe(root);
  });

  it('recovers the active tab after removing or detaching members', () => {
    const root = tabs([a, b, c], b);
    expect(activePaneView(root)).toEqual(b);
    expect(removePane(root, b)).toEqual(tabs([a, c], a));
    expect(retainPanes(root, new Set(['c']))).toEqual(pane('c'));
    expect(retainPanes(root, new Set())).toBeNull();
  });

  it('allows a card and its sections to be arranged independently', () => {
    const terminal = view('a', 'terminal'), files = view('a', 'files');
    let root = dockPane(pane('a'), terminal, a, 'right');
    root = stackPane(root, files, terminal);
    expect(paneViews(root)).toEqual([a, terminal, files]);
    expect(removePane(root, terminal)).toEqual(split(pane('a'), pane('a', 'files')));
    expect(removePane(root, a)).toEqual(tabs([terminal, files], files));
    expect(retainPanes(root, new Set(['a']))).toEqual(root);
    expect(retainPanes(root, new Set(['b']))).toBeNull();
    expect(viewKey(view('a:files'))).not.toEqual(viewKey(files));
  });

  it('retains hidden-section presentation state only for existing owners', () => {
    const layout = readWorkspaceLayout({ version: 2, root: split(pane('a'), pane('b', 'files')), hidden_sections: [view('a', 'terminal'), view('b', 'preview')] });
    expect(retainWorkspaceLayout(layout, new Set(['b']))).toEqual({ version: 2, root: pane('b', 'files'), hidden_sections: [view('b', 'preview')] });
  });

  it('validates tab membership and uniqueness by owner and section across the tree', () => {
    for (const root of [
      tabs([], a), tabs([a, a]), tabs([a, b], view('missing')),
      split(pane('a'), tabs([a, b], b)), split(pane('a', 'files'), pane('a', 'files')),
    ]) expect(readWorkspaceLayout({ version: 2, root }).root).toBeNull();
    expect(readWorkspaceLayout({ version: 2, root: tabs([a, view('a', 'files')]) }).root).not.toBeNull();
  });

  it('rejects duplicate, placed, owner-only and malformed hidden sections', () => {
    for (const hidden_sections of [[a], [view('a', 'files')], [view('b', 'files'), view('b', 'files')], [{ card_id: 'b', section_id: '' }]]) {
      expect(readWorkspaceLayout({ version: 2, root: pane('a', 'files'), hidden_sections })).toEqual({ version: 2, root: null, hidden_sections: [] });
    }
  });

  it('docks around a region, then moves an existing view without duplicating it', () => {
    const editor = view('editor'), files = view('files'), terminal = view('terminal');
    let root = dockPane(null, editor, null, 'right');
    root = dockPane(root, files, editor, 'left');
    root = dockPane(root, terminal, editor, 'bottom');
    expect(ids(root)).toEqual(['files', 'editor', 'terminal']);
    root = dockPane(root, files, terminal, 'right');
    expect(ids(root)).toEqual(['editor', 'terminal', 'files']);
    expect(root).toMatchObject({ kind: 'split', axis: 'vertical', first: pane('editor'), second: { axis: 'horizontal' } });
  });

  it('does not drop a view on itself or lose it on a stale target', () => {
    const root = dockPane(pane('a'), b, a, 'right');
    expect(dockPane(root, a, a, 'left')).toBe(root);
    expect(dockPane(root, a, view('deleted'), 'left')).toBe(root);
  });

  it('collapses removed and detached panes so remaining views fill the window', () => {
    const root = dockPane(dockPane(pane('a'), b, a, 'right'), c, b, 'bottom');
    expect(retainPanes(root, new Set(['a', 'c']))).toMatchObject({ first: pane('a'), second: pane('c') });
    expect(retainPanes(root, new Set(['c']))).toEqual(pane('c'));
    expect(removePane(pane('a'), a)).toBeNull();
  });

  it('resizes only the addressed split without expanding layout minima', () => {
    const root = dockPane(dockPane(pane('a'), b, a, 'right'), c, b, 'bottom')!;
    const resized = resizeSplit(root, '1', .2);
    expect(resized).toMatchObject({ ratio: .5, second: { ratio: .2 } });
    expect(layoutMinimum(resized)).toEqual({ width: 484, height: 404 });
    expect(layoutMinimum(resizeSplit(resized, '', .15))).toEqual(layoutMinimum(root));
    expect(layoutMinimum(resizeSplit(resized, '1', .85))).toEqual(layoutMinimum(root));
    expect(resizeSplit(root, '', 1)).toMatchObject({ ratio: .85 });
    expect(dropSide(490, 100, 500, 300)).toBe('right');
  });

  it('migrates version-1 panes and tabs without changing their arrangement', () => {
    const legacy = { version: 1, root: { kind: 'split', axis: 'horizontal', ratio: .5, first: { kind: 'pane', card_id: 'a' }, second: { kind: 'tabs', card_ids: ['b', 'c'], active_card_id: 'c' } } };
    expect(readWorkspaceLayout(legacy)).toEqual({ version: 2, root: split(pane('a'), tabs([b, c], c)), hidden_sections: [] });
    expect(legacy.version).toBe(1);
    expect(readWorkspaceLayout({ version: 1, root: { kind: 'tabs', card_ids: ['a', 'a'], active_card_id: 'a' } }).root).toBeNull();
  });

  it('rejects unsupported, malformed, overly deep and oversized persisted layouts', () => {
    let deep: WorkspaceNode = pane('0');
    for (let index = 1; index <= 16; index++) deep = split(pane(String(index)), deep);
    for (const value of [{ version: 3, root: null }, { version: 2, root: {} }, { version: 2, root: { ...split(pane('a'), pane('b')), ratio: NaN } },
      { version: 2, root: deep }, { version: 2, root: tabs(Array.from({ length: 101 }, (_, index) => view(String(index)))) },
      { version: 2, root: tabs(Array.from({ length: 100 }, (_, index) => view(String(index)))), hidden_sections: [view('a', 'files')] },
      { version: 2, root: pane('a', 'x'.repeat(201)) }]) {
      expect(readWorkspaceLayout(value).root).toBeNull();
    }
  });

  it('canonicalizes object order and omitted defaults for saved draft comparisons', () => {
    expect(JSON.stringify(readWorkspaceLayout({ root: { view: { section_id: null, card_id: 'a' }, kind: 'pane' }, version: 2 })))
      .toBe(JSON.stringify({ version: 2, root: pane('a'), hidden_sections: [] }));
  });
});
