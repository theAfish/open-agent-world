// @vitest-environment jsdom
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { CardFace } from './CardFace';
import { CatalogIcon } from './CatalogIcon';

afterEach(cleanup);

it('uses catalog artwork and layout while preserving an explicit component override', () => {
  const definition = { id: 'example.dataset', traits: [], card_face: {
    variant: 'image' as const, tone: 'sky' as const, image_url: '/api/plugins/example/assets/landscape',
  } };
  const { container, rerender, getByAltText } = render(<CardFace definition={definition} icon={<i />} label="Landscapes" imageAlt="Mountains" />);
  expect(container.querySelector('.card-face')?.getAttribute('data-variant')).toBe('image');
  expect(getByAltText('Mountains').getAttribute('src')).toBe(definition.card_face.image_url);
  expect(container.querySelector('[data-design="archival"]')).toBeNull();
  rerender(<CardFace definition={definition} variant="compact" tone="sand" icon={<i />} label="Landscapes" />);
  expect(container.querySelector('.card-face')?.getAttribute('data-tone')).toBe('sand');
  expect(container.querySelector('img')).toBeNull();
});

it('prints only collection identity and the name, keeping functional descriptions off the default face', () => {
  const { container } = render(<CardFace definition={{ id: 'oaw.barracks.summoner', traits: [], deck_label: 'Tools' }}
    icon={<svg />} label="Summoning" description="Equip on an Agent to summon." badge="Internal metadata" />);
  expect(container.querySelector('.card-face-edition')?.textContent).toBe('OAWTools');
  expect(container.querySelector('.card-face-copy')?.textContent).toBe('Summoning');
  expect(container.querySelector('.card-print-art, .card-face-detail, .card-face-badge, small')).toBeNull();
  expect(container.querySelector('[data-illustration="summoning"]')).toBeTruthy();
});

it('retains an unknown card icon and gives authored artwork precedence over builtin illustrations', () => {
  const { container, rerender, getByTestId } = render(<CardFace definition={{ id: 'example.unknown', traits: [] }}
    icon={<svg data-testid="plugin-symbol" />} label="A new card" />);
  expect(getByTestId('plugin-symbol')).toBeTruthy();
  expect(container.querySelector('[data-design="archival"]')).toBeTruthy();
  rerender(<CardFace definition={{ id: 'agent', traits: [] }} icon={<svg />} label="My print"
    artwork={<svg data-testid="authored-print" />} description="An authored description" />);
  expect(getByTestId('authored-print')).toBeTruthy();
  expect(container.querySelector('[data-design="archival"], [data-illustration]')).toBeNull();
  expect(container.querySelector('small')?.textContent).toBe('An authored description');
});

it('falls back to an icon after an image fails and tries a replacement URL', () => {
  const props = { icon: <svg data-testid="fallback" />, label: 'Landscape', variant: 'image' as const };
  const { container, rerender, getByTestId } = render(<CardFace {...props} imageUrl="/missing.png" />);
  fireEvent.error(container.querySelector('img')!);
  expect(container.querySelector('img')).toBeNull();
  expect(getByTestId('fallback')).toBeTruthy();
  rerender(<CardFace {...props} imageUrl="/replacement.png" />);
  expect(container.querySelector('img')?.getAttribute('src')).toBe('/replacement.png');
});

it('preserves branded Agent assets and custom symbols ahead of the generic Agent illustration', () => {
  const codex = { id: 'openai.codex.agent', traits: ['core.agent'], icon: 'bot', icon_url: '/api/plugins/openai.codex/assets/logo' };
  const { container, rerender } = render(<CardFace definition={codex} icon={<CatalogIcon definition={codex} />} label="Codex Agent" />);
  expect(container.querySelector('.catalog-asset-icon')?.getAttribute('style')).toContain(codex.icon_url);
  expect(container.querySelector('[data-illustration]')).toBeNull();
  const custom = { id: 'custom.agent', traits: ['core.agent'], icon: 'atom' };
  rerender(<CardFace definition={custom} icon={<CatalogIcon definition={custom} />} label="Scientific Agent" />);
  expect(container.querySelector('.lucide-atom')).toBeTruthy();
  expect(container.querySelector('[data-illustration]')).toBeNull();
});

it('gives legacy catalogs different layouts without changing their stored data', () => {
  const { container, rerender } = render(<CardFace definition={{ id: 'text', traits: [] }} icon={<i />} label="笔记" />);
  expect(container.querySelector('.card-face')?.getAttribute('data-variant')).toBe('text');
  rerender(<CardFace definition={{ id: 'sandbox', traits: [] }} icon={<i />} label="Console" />);
  expect(container.querySelector('.card-face')?.getAttribute('data-variant')).toBe('dark');
});
