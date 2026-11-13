// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest';
import type { PointerEvent } from 'react';
import { updateConnectionHoverHint } from './ConnectionHoverHint';

afterEach(() => document.body.replaceChildren());

it('shows the hint only for the actual handle on its own card', () => {
  const container = document.createElement('section');
  container.className = 'container-frame';
  container.innerHTML = '<article class="world-card"><div class="semantic-handle"></div><button>Action</button></article>';
  document.body.append(container);
  const card = container.firstElementChild as HTMLElement;
  Object.defineProperties(card, { offsetWidth: { value: 100 }, offsetHeight: { value: 100 } });
  card.getBoundingClientRect = () => new DOMRect(0, 0, 100, 100);
  const event = { target: card.firstElementChild, buttons: 0, clientX: 106, clientY: 50 } as unknown as PointerEvent<HTMLElement>;
  updateConnectionHoverHint(event, card);
  expect(card.dataset.connectionHot).toBe('true');
  expect(Number.parseFloat(card.style.getPropertyValue('--connection-hint-x'))).toBeCloseTo(100, 5);
  updateConnectionHoverHint(event, container);
  expect(container.dataset.connectionHot).toBeUndefined();
  updateConnectionHoverHint({ ...event, target: card.lastElementChild } as PointerEvent<HTMLElement>, card);
  expect(card.dataset.connectionHot).toBeUndefined();
});

it('clears the hint during an existing gesture without measuring geometry', () => {
  const card = document.createElement('article');
  card.dataset.connectionHot = 'true';
  card.getBoundingClientRect = () => { throw new Error('No layout reads during a drag'); };
  updateConnectionHoverHint({ buttons: 1 } as PointerEvent<HTMLElement>, card);
  expect(card.dataset.connectionHot).toBeUndefined();
});
