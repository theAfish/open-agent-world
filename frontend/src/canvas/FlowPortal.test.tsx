// @vitest-environment jsdom
import { expect, it } from 'vitest';
import { ownedFlowPortal } from './FlowPortal';

it.each(['react-flow__viewport-portal', 'react-flow__edgelabel-renderer'])('keeps %s in its owning graph when inner graphs precede it', className => {
  const root = document.createElement('div');
  root.className = 'react-flow';
  root.innerHTML = `<div class="react-flow"><div class="${className}" data-owner="inner"></div></div><div class="${className}" data-owner="outer"></div>`;
  expect(root.querySelector(`.${className}`)?.getAttribute('data-owner')).toBe('inner');
  expect(ownedFlowPortal(root, className)?.getAttribute('data-owner')).toBe('outer');
  expect(ownedFlowPortal(root.firstElementChild as HTMLElement, className)?.getAttribute('data-owner')).toBe('inner');
  root.lastElementChild!.remove();
  expect(ownedFlowPortal(root, className)).toBeNull();
});
