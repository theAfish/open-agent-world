import type { TutorialStep } from './steps';

export interface InteractionScope {
  active: boolean;
  busy: boolean;
  step: TutorialStep;
  refs: Partial<Record<string, string>>;
  selectedIds?: string[];
}
let currentScope: (() => InteractionScope) | undefined;
/** Pointer previews bypass native drag events, but share the same placement boundary. */
export function tutorialAllowsDrop(target: Element): boolean {
  const scope = currentScope?.();
  return !scope || tutorialAllows(target, scope, 'drop');
}
/** Gesture target filtering complements event capture: a released drag must still clean up. */
export function tutorialAllowsCanvasTarget(id: string, action: 'connect' | 'glue' | 'transform'): boolean {
  const scope = currentScope?.();
  if (!scope?.active) return true;
  if (!scope.busy && action === 'transform' && scope.step.expects === 'promotion')
    return (scope.step.participants ?? []).some(role => scope.refs[role] === id);
  if (scope.busy || scope.step.expects !== action) return false;
  return (scope.step.participants ?? []).some(role => scope.refs[role] === id);
}
const editable = 'input, textarea, select, [contenteditable="true"]';
const controls = 'button, a, input, textarea, select, [tabindex], [contenteditable="true"]';

/** Interaction ownership is independent of the visual mask (drag steps have none). */
export function tutorialAllows(target: Element, scope: InteractionScope, kind = 'click'): boolean {
  if (!scope.active || target.closest('.tutorial-guide')) return true;
  if (scope.busy) return false;
  const { step, refs } = scope;
  const within = (selector: string) => Boolean(target.closest(selector));
  const isCanvas = within('.react-flow__pane');
  if (step.id.startsWith('legion-')) {
    const workspace = target.closest('[data-legion-workspace]');
    if (workspace) {
      if (workspace.getAttribute('data-legion-workspace') !== refs.legion) return false;
      if (step.id === 'legion-layout' || step.id === 'legion-return') return within('.legion-layout-palette, .legion-layout-stage, .legion-window-close-prompt, .legion-window-error, [data-tutorial="legion-edit"], [data-tutorial="legion-save"], [data-tutorial="legion-reset"], [data-tutorial="legion-back"]')
        && (kind === 'wheel' || within('.workspace-section-controls') || !within('.legion-pane-content'));
      return false;
    }
    if (within('[data-tutorial="legion-open"]')) return target.closest('.react-flow__node')?.getAttribute('data-id') === refs.legion;
    if (step.id === 'legion-form') {
      if (within('[data-tutorial="legion-selection"]')) {
        const expected = (step.participants ?? []).map(role => refs[role]);
        return scope.selectedIds?.length === expected.length && expected.every(id => !!id && scope.selectedIds?.includes(id));
      }
      if (isCanvas) return true;
      return (step.participants ?? []).some(role => refs[role] === target.closest('.react-flow__node')?.getAttribute('data-id'))
        && !within('button, input, textarea, select, .semantic-handle, .surface-resize-arc');
    }
    return false;
  }
  if (step.target === 'minister' && target.closest('[data-minister-for]')?.getAttribute('data-minister-for') === refs.minister) return true;
  if (kind === 'wheel') return isCanvas || within('.settings-dialog, .card-library-modal, [data-tutorial-highlight]');
  if (step.id === 'pan') return isCanvas;
  if (step.id === 'zoom') return isCanvas || within('.world-controls');
  if (step.target === 'deck') {
    const type = step.role === 'ministerRole' ? 'core.minister-role' : step.role === 'agent' ? 'agent' : step.role === 'conversation' ? 'conversation' : step.role === 'sandbox' ? 'sandbox' : 'text';
    return within(`[data-palette-card="${type}"]`) || (kind === 'drop' && isCanvas);
  }
  if (step.id === 'deck-build') return within('.component-palette, .library-card[data-tutorial-highlight]');
  if (step.target.startsWith('model-') || step.id === 'model-settings') {
    if (within('[data-tutorial="settings"], [data-tutorial="models-tab"]')) return true;
    // The close control remains a recovery route; the guide then points to Settings.
    if (within('[data-tutorial="settings-close"]')) return true;
    if (step.target === 'model-connection') return within('[data-tutorial="model-connection"], .connection-list');
    if (step.target === 'model-credentials') return within('[data-tutorial="model-credentials"], .connection-advanced, [data-tutorial="model-connection"]');
    if (step.target === 'model-list') return within('[data-tutorial="model-list"], [data-tutorial="model-connection"]');
    if (step.target === 'model-save') return within('[data-tutorial="model-save"], .model-editor');
    return false;
  }
  if (step.target === 'library' || step.target.startsWith('library-')) return within('[data-tutorial-highlight]');
  if (step.participants && within('.connection-dialog')) return true;
  if (step.target === 'tools' && within('[data-tutorial="tools"]')) return true;
  const node = target.closest('.react-flow__node');
  const roles = step.participants ?? (step.role ? [step.role] : [step.target]);
  const ownsNode = node && roles.some(role => refs[role] === node.getAttribute('data-id'));
  if (ownsNode) {
    if (within('.surface-resize-arc, .minister-radius-handle')) return false;
    if (within('.card-name-input')) return false;
    if (step.expects === 'configure' && within('button') && !within('.model-select')) return false;
    if (within('.semantic-handle')) return step.expects === 'connect';
    if (within('.icon-button--danger')) return step.expects === 'delete';
    if (['move', 'select', 'focus', 'delete'].includes(step.expects ?? '')) return !within('button, a, input, textarea, select, [contenteditable="true"]');
    if (step.expects === 'close') return within('.node-surface-close');
    if (['open', 'workspace'].includes(step.expects ?? '')) return !within('button, a, input, textarea, select, [contenteditable="true"]') || within('.card-expand-button');
    return true;
  }
  if (step.target === 'minister' && target.closest('.minister-presence')?.getAttribute('data-tutorial-card-id') === refs.minister) return true;
  return false;
}

export function installTutorialInteractionGuard(getScope: () => InteractionScope, pause: () => void) {
  currentScope = getScope;
  let pointerGesture = false;
  const stop = (event: Event) => { event.preventDefault(); event.stopImmediatePropagation(); };
  const intercept = (event: Event) => {
    const scope = getScope();
    if (!scope.active) { pointerGesture = false; return; }
    const target = event.target instanceof Element ? event.target : document.body;
    if (event.type === 'submit') {
      if (target.closest('.settings-dialog') && (scope.busy || scope.step.id !== 'model-save')) stop(event);
      else if (!target.closest('.settings-dialog') && !tutorialAllows(target, scope)) stop(event);
      return;
    }
    if (['pointerover', 'pointerenter'].includes(event.type)) {
      if (target.closest('.world-card.is-node[data-minister]') && scope.step.target !== 'minister') { stop(event); return; }
      if (target.closest('.minister-avatar-zone') && !tutorialAllows(target, scope)) stop(event);
      return;
    }
    if (event.type === 'keydown') {
      const key = event as KeyboardEvent;
      if (key.key === 'Escape') {
        if (document.body.classList.contains('is-palette-dragging')) return;
        stop(event); if (!scope.busy) pause(); return;
      }
      if (key.key === 'Tab') {
        const allowed = [...document.querySelectorAll<HTMLElement>(controls)].filter(el => el.tabIndex >= 0 && !el.matches(':disabled') && el.getClientRects().length && tutorialAllows(el, scope));
        const index = allowed.indexOf(document.activeElement as HTMLElement);
        const next = allowed[(index + (key.shiftKey ? -1 : 1) + allowed.length) % allowed.length];
        stop(event); next?.focus(); return;
      }
      if (tutorialAllows(target, scope) && (target.closest(editable) || target.closest('.tutorial-guide'))) return;
      const plain = !key.ctrlKey && !key.metaKey && !key.altKey;
      if (scope.step.id === 'legion-form' && ['Control', 'Meta'].includes(key.key)) return;
      const subjectId = scope.step.role && scope.refs[scope.step.role];
      const subjectSelected = subjectId && scope.selectedIds?.length && scope.selectedIds.every(id => id === subjectId);
      if (plain && (key.key === 'Shift' || (subjectSelected && scope.step.expects === 'focus' && key.key.toLowerCase() === 'f')
        || (subjectSelected && scope.step.expects === 'delete' && ['Delete', 'Backspace'].includes(key.key)))) return;
      if (plain && ['Enter', ' '].includes(key.key) && tutorialAllows(target, scope)) return;
      stop(event); return;
    }
    // Finish a gesture accepted at its source, even across a step transition.
    if (['pointermove', 'mousemove', 'pointerup', 'mouseup', 'pointercancel', 'dragend'].includes(event.type)) {
      if (pointerGesture) {
        if (['mouseup', 'pointercancel', 'dragend'].includes(event.type)) pointerGesture = false;
        return;
      }
      if (event.type.endsWith('move')) return;
    }
    const kind = ['drop', 'dragover', 'dragenter'].includes(event.type) ? 'drop' : event.type;
    const allowed = tutorialAllows(target, scope, kind);
    if (event.type === 'pointerdown') pointerGesture = allowed;
    if (!allowed) stop(event);
  };
  const events = ['submit', 'pointerover', 'pointerenter', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'pointermove', 'mousemove', 'pointercancel', 'click', 'dblclick', 'contextmenu', 'dragstart', 'dragenter', 'dragover', 'drop', 'dragend', 'keydown', 'wheel'];
  for (const name of events) window.addEventListener(name, intercept, { capture: true, passive: false });
  return () => { if (currentScope === getScope) currentScope = undefined; for (const name of events) window.removeEventListener(name, intercept, true); };
}
