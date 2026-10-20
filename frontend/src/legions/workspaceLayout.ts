import { workspaceLayoutSize, type WorkspaceLayoutMetadata } from '../workspace/workspaceLayoutMetadata';

/** Layouts reference a card or one of its sections; they never copy runtime ownership. */
export interface WorkspaceView { card_id: string; section_id?: string }
export type WorkspaceLeaf = { kind: 'pane'; view: WorkspaceView }
  | { kind: 'tabs'; views: WorkspaceView[]; active_view: WorkspaceView };
export type WorkspaceNode = WorkspaceLeaf | {
  kind: 'split'; axis: 'horizontal' | 'vertical'; ratio: number;
  first: WorkspaceNode; second: WorkspaceNode;
};
export interface WorkspaceLayout { version: 2; root: WorkspaceNode | null; hidden_sections: WorkspaceView[] }
export type DockSide = 'left' | 'right' | 'top' | 'bottom';

export function viewKey(view: WorkspaceView): string {
  return JSON.stringify([view.card_id, view.section_id ?? null]);
}

export function readWorkspaceLayout(value: unknown): WorkspaceLayout {
  const empty: WorkspaceLayout = { version: 2, root: null, hidden_sections: [] };
  if (!value || typeof value !== 'object' || !('version' in value) || (value.version !== 1 && value.version !== 2) || !('root' in value)) return empty;
  const legacy = value.version === 1;
  const seen = new Set<string>();
  function readView(raw: unknown): WorkspaceView | null {
    if (!raw || typeof raw !== 'object' || !('card_id' in raw) || typeof raw.card_id !== 'string' || !raw.card_id || raw.card_id.length > 200) return null;
    const section = 'section_id' in raw ? raw.section_id : undefined;
    if (section != null && (typeof section !== 'string' || !section || section.length > 200)) return null;
    return section == null ? { card_id: raw.card_id } : { card_id: raw.card_id, section_id: section as string };
  }
  function addView(raw: unknown): WorkspaceView | null {
    const view = readView(raw);
    if (!view || seen.has(viewKey(view)) || seen.size >= 100) return null;
    seen.add(viewKey(view));
    return view;
  }
  function readNode(raw: unknown, depth: number): WorkspaceNode | null {
    if (!raw || typeof raw !== 'object' || depth > 16 || !('kind' in raw)) return null;
    if (raw.kind === 'pane') {
      const view = addView(legacy && 'card_id' in raw ? { card_id: raw.card_id } : 'view' in raw ? raw.view : null);
      return view ? { kind: 'pane', view } : null;
    }
    if (raw.kind === 'tabs') {
      const items = legacy && 'card_ids' in raw && Array.isArray(raw.card_ids) ? raw.card_ids.map(card_id => ({ card_id })) : 'views' in raw ? raw.views : null;
      const active = readView(legacy && 'active_card_id' in raw ? { card_id: raw.active_card_id } : 'active_view' in raw ? raw.active_view : null);
      if (!Array.isArray(items) || !items.length || !active) return null;
      const views: WorkspaceView[] = [];
      for (const item of items) {
        const view = addView(item);
        if (!view) return null;
        views.push(view);
      }
      return views.some(view => viewKey(view) === viewKey(active)) ? { kind: 'tabs', views, active_view: active } : null;
    }
    if (raw.kind !== 'split' || !('axis' in raw) || (raw.axis !== 'horizontal' && raw.axis !== 'vertical')
      || !('ratio' in raw) || typeof raw.ratio !== 'number' || !Number.isFinite(raw.ratio) || raw.ratio < .15 || raw.ratio > .85
      || !('first' in raw) || !('second' in raw)) return null;
    const first = readNode(raw.first, depth + 1), second = readNode(raw.second, depth + 1);
    return first && second ? { kind: 'split', axis: raw.axis, ratio: raw.ratio, first, second } : null;
  }
  const root = value.root === null ? null : readNode(value.root, 1);
  if (value.root !== null && !root) return empty;
  const hidden = 'hidden_sections' in value ? value.hidden_sections : [];
  if (!Array.isArray(hidden)) return empty;
  const hidden_sections: WorkspaceView[] = [];
  for (const item of hidden) {
    const view = addView(item);
    if (!view?.section_id) return empty;
    hidden_sections.push(view);
  }
  return { version: 2, root, hidden_sections };
}

export function paneViews(node: WorkspaceNode | null): WorkspaceView[] {
  return !node ? [] : node.kind === 'pane' ? [node.view] : node.kind === 'tabs' ? node.views : [...paneViews(node.first), ...paneViews(node.second)];
}

export function activePaneView(node: WorkspaceLeaf): WorkspaceView {
  return node.kind === 'pane' ? node.view : node.active_view;
}

function includesView(views: WorkspaceView[], view: WorkspaceView): boolean {
  return views.some(item => viewKey(item) === viewKey(view));
}

function makeLeaf(views: WorkspaceView[], active: WorkspaceView): WorkspaceLeaf | null {
  return !views.length ? null : views.length === 1 ? { kind: 'pane', view: views[0] }
    : { kind: 'tabs', views, active_view: includesView(views, active) ? active : views[0] };
}

export function leafForView(node: WorkspaceNode | null, view: WorkspaceView): WorkspaceLeaf | null {
  if (!node) return null;
  return node.kind === 'split' ? leafForView(node.first, view) ?? leafForView(node.second, view)
    : includesView(paneViews(node), view) ? node : null;
}

function mapLeaves(node: WorkspaceNode, update: (leaf: WorkspaceLeaf) => WorkspaceNode): WorkspaceNode {
  return node.kind === 'split' ? { ...node, first: mapLeaves(node.first, update), second: mapLeaves(node.second, update) } : update(node);
}

export function activateTab(root: WorkspaceNode, view: WorkspaceView): WorkspaceNode {
  return mapLeaves(root, leaf => leaf.kind === 'tabs' && includesView(leaf.views, view) ? { ...leaf, active_view: view } : leaf);
}

/** Move a view into a region, optionally before an existing tab (also reorders). */
export function stackPane(root: WorkspaceNode | null, view: WorkspaceView, target: WorkspaceView, before?: WorkspaceView): WorkspaceNode | null {
  const leaf = leafForView(root, target);
  if (!leaf || (before && viewKey(before) === viewKey(view))) return root;
  const targetViews = paneViews(leaf).filter(item => viewKey(item) !== viewKey(view));
  if (!targetViews.length) return root;
  const anchor = targetViews[0];
  const remaining = removePane(root, view)!;
  const index = before ? targetViews.findIndex(item => viewKey(item) === viewKey(before)) : -1;
  targetViews.splice(index < 0 ? targetViews.length : index, 0, view);
  const result = mapLeaves(remaining, item => includesView(paneViews(item), anchor) ? makeLeaf(targetViews, view)! : item);
  return readWorkspaceLayout({ version: 2, root: result }).root ? result : root;
}

function filterPanes(node: WorkspaceNode | null, keep: (view: WorkspaceView) => boolean): WorkspaceNode | null {
  if (!node) return null;
  if (node.kind === 'pane') return keep(node.view) ? node : null;
  if (node.kind === 'tabs') return makeLeaf(node.views.filter(keep), node.active_view);
  const first = filterPanes(node.first, keep), second = filterPanes(node.second, keep);
  return first && second ? { ...node, first, second } : first ?? second;
}

/** Detaching a member removes its whole-card view and every section owned by it. */
export function retainPanes(node: WorkspaceNode | null, memberIds: ReadonlySet<string>): WorkspaceNode | null {
  return filterPanes(node, view => memberIds.has(view.card_id));
}

export function retainWorkspaceLayout(layout: WorkspaceLayout, memberIds: ReadonlySet<string>): WorkspaceLayout {
  return { ...layout, root: retainPanes(layout.root, memberIds), hidden_sections: layout.hidden_sections.filter(view => memberIds.has(view.card_id)) };
}

export function removePane(node: WorkspaceNode | null, view: WorkspaceView): WorkspaceNode | null {
  return filterPanes(node, item => viewKey(item) !== viewKey(view));
}

export function dockPane(root: WorkspaceNode | null, view: WorkspaceView, target: WorkspaceView | null, side: DockSide): WorkspaceNode | null {
  if (root && (!target || !includesView(paneViews(root), target))) return root;
  if (target && viewKey(view) === viewKey(target)) {
    target = paneViews(leafForView(root, view)).find(item => viewKey(item) !== viewKey(view)) ?? null;
    if (!target) return root;
  }
  const pane: WorkspaceNode = { kind: 'pane', view };
  const remaining = removePane(root, view);
  if (!remaining) return pane;
  const before = side === 'left' || side === 'top';
  function insert(node: WorkspaceNode): WorkspaceNode {
    if (node.kind !== 'split') return includesView(paneViews(node), target!) ? {
      kind: 'split', axis: side === 'left' || side === 'right' ? 'horizontal' : 'vertical', ratio: .5,
      first: before ? pane : node, second: before ? node : pane,
    } : node;
    return { ...node, first: insert(node.first), second: insert(node.second) };
  }
  const result = insert(remaining);
  // Keep UI and backend depth/count limits identical; an invalid drop is a no-op.
  return readWorkspaceLayout({ version: 2, root: result }).root ? result : root;
}

export function resizeSplit(node: WorkspaceNode, path: string, ratio: number): WorkspaceNode {
  if (node.kind !== 'split') return node;
  if (!path) return { ...node, ratio: Math.min(.85, Math.max(.15, ratio)) };
  return path[0] === '0' ? { ...node, first: resizeSplit(node.first, path.slice(1), ratio) }
    : { ...node, second: resizeSplit(node.second, path.slice(1), ratio) };
}

export const WORKSPACE_DIVIDER_SIZE = 4;

export function layoutMinimum(node: WorkspaceNode | null, preference?: (view: WorkspaceView) => WorkspaceLayoutMetadata | undefined): { width: number; height: number } {
  if (!node) return workspaceLayoutSize().minimum;
  if (node.kind !== 'split') {
    // A tab switch must not resize the workspace or make another tab unusable.
    return paneViews(node).map(view => workspaceLayoutSize(preference?.(view)).minimum)
      .reduce((size, next) => ({ width: Math.max(size.width, next.width), height: Math.max(size.height, next.height) }), { width: 0, height: 0 });
  }
  const a = layoutMinimum(node.first, preference), b = layoutMinimum(node.second, preference);
  // Resizing redistributes the available space; it must not grow the workspace.
  // Each grid track enforces its subtree minimum independently of the ratio.
  return node.axis === 'horizontal'
    ? { width: a.width + b.width + WORKSPACE_DIVIDER_SIZE, height: Math.max(a.height, b.height) }
    : { width: Math.max(a.width, b.width), height: a.height + b.height + WORKSPACE_DIVIDER_SIZE };
}

export function dropSide(x: number, y: number, width: number, height: number): DockSide {
  const distances: [DockSide, number][] = [['left', x / width], ['right', 1 - x / width], ['top', y / height], ['bottom', 1 - y / height]];
  return distances.sort((a, b) => a[1] - b[1])[0][0];
}
