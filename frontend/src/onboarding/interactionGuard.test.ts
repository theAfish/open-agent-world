// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { installTutorialInteractionGuard, tutorialAllowsCanvasTarget, tutorialAllows, tutorialAllowsDrop, type InteractionScope } from './interactionGuard';
import { STEPS } from './steps';
const scope = (id: string): InteractionScope => ({ active: true, busy: false, step: STEPS.find(s => s.id === id)!, refs: { practice: 'practice', agent: 'agent', sandbox: 'sandbox' } });
function el(html: string) { document.body.innerHTML = html; return document.body.firstElementChild!; }
afterEach(() => { document.body.innerHTML = ''; });
describe('tutorial interaction ownership', () => {
  it('forms only the three tutorial cards and keeps workspace guidance inside its owner', () => {
    const current = { ...scope('legion-form'), refs: { agent: 'a', conversation: 'c', sandbox: 's', legion: 'g' }, selectedIds: ['a', 'c'] };
    const form = el('<button data-tutorial="legion-selection" />');
    expect(tutorialAllows(form, current)).toBe(false);
    current.selectedIds = ['a', 'c', 'other'];
    expect(tutorialAllows(form, current)).toBe(false);
    current.selectedIds = ['a', 'c', 's'];
    expect(tutorialAllows(form, current)).toBe(true);
    const workspace = { ...current, step: STEPS.find(step => step.id === 'legion-layout')! };
    const dock = el('<dialog data-legion-workspace="g"><div class="legion-layout-stage"><button /></div></dialog>').querySelector('button')!;
    expect(tutorialAllows(dock, workspace)).toBe(true);
    dock.closest('dialog')!.setAttribute('data-legion-workspace', 'other');
    expect(tutorialAllows(dock, workspace)).toBe(false);
    const hint = el('<dialog data-legion-workspace="g"><div class="tutorial-guide"><button /></div></dialog>').querySelector('button')!;
    expect(tutorialAllows(hint, workspace)).toBe(true);
  });
  it('excludes unrelated automatic glue and connection targets', () => {
    let current = { ...scope('glue'), refs: { glueA: 'a', glueB: 'b' } } as InteractionScope;
    const remove = installTutorialInteractionGuard(() => current, () => {});
    try {
      expect(tutorialAllowsCanvasTarget('b', 'glue')).toBe(true);
      expect(tutorialAllowsCanvasTarget('unrelated', 'glue')).toBe(false);
      expect(tutorialAllowsCanvasTarget('b', 'transform')).toBe(false);
      current = scope('sandbox-connect');
      expect(tutorialAllowsCanvasTarget('sandbox', 'connect')).toBe(true);
      expect(tutorialAllowsCanvasTarget('other', 'connect')).toBe(false);
      current.active = false;
      expect(tutorialAllowsCanvasTarget('other', 'connect')).toBe(true);
    } finally { remove(); }
    expect(tutorialAllowsCanvasTarget('other', 'glue')).toBe(true);
  });
  it('allows only the requested deck card and a canvas drop', () => {
    expect(tutorialAllows(el('<button data-palette-card="text" />'), scope('place'))).toBe(true);
    expect(tutorialAllows(el('<button data-palette-card="agent" />'), scope('place'))).toBe(false);
    const canvas = el('<div class="react-flow__pane" />');
    expect(tutorialAllows(canvas, scope('place'))).toBe(false);
    expect(tutorialAllows(canvas, scope('place'), 'drop')).toBe(true);
  });
  it('applies the current tutorial boundary to pointer drops and lets Escape cancel a preview first', () => {
    let current = scope('place');
    const pause = vi.fn();
    const remove = installTutorialInteractionGuard(() => current, pause);
    try {
      const canvas = el('<div class="react-flow__pane" />');
      expect(tutorialAllowsDrop(canvas)).toBe(true);
      expect(tutorialAllowsDrop(el('<div class="deck-trash" />'))).toBe(false);
      current = { ...current, busy: true };
      expect(tutorialAllowsDrop(canvas)).toBe(false);
      current = scope('place');
      document.body.classList.add('is-palette-dragging');
      const key = () => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', cancelable: true }));
      expect(key()).toBe(true);
      expect(pause).not.toHaveBeenCalled();
      document.body.classList.remove('is-palette-dragging');
      expect(key()).toBe(false);
      expect(pause).toHaveBeenCalledOnce();
    } finally { document.body.classList.remove('is-palette-dragging'); remove(); }
  });
  it('keeps model inputs available but blocks other settings', () => {
    const input = el('<div data-tutorial="model-list"><input /></div>').firstElementChild!;
    expect(tutorialAllows(input, scope('model-list'))).toBe(true);
    expect(tutorialAllows(el('<button data-tutorial="model-save" />'), scope('model-list'))).toBe(false);
    expect(tutorialAllows(el('<button data-tutorial="settings-close" />'), scope('model-list'))).toBe(true);
  });
  it('allows both connection endpoints and the capability chooser', () => {
    for (const id of ['agent', 'sandbox']) {
      const handle = el(`<div class="react-flow__node" data-id="${id}"><div class="semantic-handle" /></div>`).firstElementChild!;
      expect(tutorialAllows(handle, scope('sandbox-connect'))).toBe(true);
      expect(tutorialAllows(handle, scope('configure'))).toBe(false);
    }
    expect(tutorialAllows(el('<div class="connection-dialog" />'), scope('sandbox-connect'))).toBe(true);
  });
  it('allows dragging the focusable React Flow node surface', () => {
    const icon = el('<div class="react-flow__node" data-id="practice" tabindex="0"><div class="card-kind-icon" /></div>').firstElementChild!;
    expect(tutorialAllows(icon, scope('move'))).toBe(true);
  });
  it('blocks deleting a target outside the delete step', () => {
    const button = el('<div class="react-flow__node" data-id="practice"><button class="icon-button--danger" /></div>').firstElementChild!;
    expect(tutorialAllows(button, scope('open'))).toBe(false);
    expect(tutorialAllows(button, scope('delete'))).toBe(true);
  });
  it('blocks unrelated shortcuts, permits editing, pauses and cleans up', () => {
    let current = scope('model-list');
    const pause = vi.fn(() => { current = { ...current, active: false }; });
    const remove = installTutorialInteractionGuard(() => current, pause);
    try {
      const key = (target: Element, value: string, ctrlKey = false) => target.dispatchEvent(new KeyboardEvent('keydown', { key: value, ctrlKey, bubbles: true, cancelable: true }));
      expect(key(document.body, 'Delete')).toBe(false);
      expect(key(document.body, 'v', true)).toBe(false);
      const input = el('<div data-tutorial="model-list"><input /></div>').firstElementChild!;
      expect(key(input, 'v', true)).toBe(true);
      expect(key(input, 'Escape')).toBe(false);
      expect(pause).toHaveBeenCalledOnce();
      expect(key(document.body, 'v', true)).toBe(true);
      current = scope('place');
    } finally { remove(); }
    expect(document.body.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))).toBe(true);
  });
  it('prevents Enter from saving model settings before the save step', () => {
    let current = scope('model-list');
    const remove = installTutorialInteractionGuard(() => current, () => {});
    const form = el('<form class="settings-dialog"><input /></form>');
    const submit = () => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    try {
      expect(submit()).toBe(false);
      current = scope('model-save');
      expect(submit()).toBe(true);
    } finally { remove(); }
  });
  it('only permits Delete when the tutorial subject is the entire selection', () => {
    const current = { ...scope('delete'), selectedIds: ['practice', 'unrelated'] };
    const remove = installTutorialInteractionGuard(() => current, () => {});
    const press = () => document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true, cancelable: true }));
    try {
      expect(press()).toBe(false);
      current.selectedIds = ['practice'];
      expect(press()).toBe(true);
    } finally { remove(); }
  });
  it('finishes a valid drag across a step transition', () => {
    let current = scope('place');
    const remove = installTutorialInteractionGuard(() => current, () => {});
    try {
      const source = el('<button data-palette-card="text" />');
      expect(source.dispatchEvent(new Event('pointerdown', { bubbles: true, cancelable: true }))).toBe(true);
      current = scope('move');
      expect(document.body.dispatchEvent(new Event('pointerup', { bubbles: true, cancelable: true }))).toBe(true);
    } finally { remove(); }
  });
});
