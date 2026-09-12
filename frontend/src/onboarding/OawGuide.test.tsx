// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import canonicalLogo from '../../../docs/assets/logo.svg?raw';
import { OawGuide } from './OawGuide';

beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1));
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('keeps the canonical welcome logo, then exposes two skinned legs and one circular head', () => {
  const source = new DOMParser().parseFromString(canonicalLogo, 'image/svg+xml');
  const { container, rerender } = render(<OawGuide inLogo />);
  expect(container.querySelector('[data-rig="profile"] path')?.getAttribute('d')?.replace(/\s+/g, ' ')).toBe(source.querySelector('path')?.getAttribute('d')?.replace(/\s+/g, ' '));
  expect(container.querySelector('[data-rig="profile"]')?.getAttribute('opacity')).toBe('1');
  for (const motion of ['enter', 'idle', 'walk', 'indicate', 'celebrate', 'think', 'speak'] as const) {
    rerender(<OawGuide motion={motion} />);
    const head = container.querySelector('[data-rig="head"]');
    expect(head?.getAttribute('r')).toBe('81');
    expect(head?.getAttribute('fill')).toBe('#192638');
    expect(head?.getAttribute('cx')).toBe('627');
    expect(container.querySelectorAll('[data-rig="left-leg"], [data-rig="right-leg"]')).toHaveLength(2);
    expect(container.querySelector('[data-rig="front"]')?.getAttribute('opacity')).toBe('1');
    expect(container.querySelector('[data-rig="profile"]')?.getAttribute('opacity')).toBe('0');
  }
});
it('scopes every mask and gradient when the ring and guide share a canvas', () => {
  const { container } = render(<><OawGuide ringOnly /><OawGuide logo /><OawGuide /></>);
  const ids = [...container.querySelectorAll('[id]')].map(element => element.id);
  expect(new Set(ids).size).toBe(ids.length);
  for (const element of container.querySelectorAll('[clip-path], [mask], [stroke]')) {
    const value = element.getAttribute('clip-path') ?? element.getAttribute('mask') ?? element.getAttribute('stroke');
    const id = value?.match(/^url\(#(.+)\)$/)?.[1];
    if (id) expect(ids).toContain(id);
  }
});
