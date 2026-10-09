// @vitest-environment jsdom
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { CardFace } from './CardFace';

afterEach(cleanup);

it('uses catalog artwork and layout while preserving an explicit component override', () => {
  const definition = { id: 'example.dataset', traits: [], card_face: {
    variant: 'image' as const, tone: 'sky' as const, image_url: '/api/plugins/example/assets/landscape',
  } };
  const { container, rerender, getByAltText } = render(<CardFace definition={definition} icon={<i />} label="Landscapes" imageAlt="Mountains" />);
  expect(container.querySelector('.card-face')?.getAttribute('data-variant')).toBe('image');
  expect(getByAltText('Mountains').getAttribute('src')).toBe(definition.card_face.image_url);
  rerender(<CardFace definition={definition} variant="compact" tone="sand" icon={<i />} label="Landscapes" />);
  expect(container.querySelector('.card-face')?.getAttribute('data-tone')).toBe('sand');
  expect(container.querySelector('img')).toBeNull();
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

it('gives legacy catalogs different layouts without changing their stored data', () => {
  const { container, rerender } = render(<CardFace definition={{ id: 'text', traits: [] }} icon={<i />} label="笔记" />);
  expect(container.querySelector('.card-face')?.getAttribute('data-variant')).toBe('text');
  rerender(<CardFace definition={{ id: 'sandbox', traits: [] }} icon={<i />} label="Console" />);
  expect(container.querySelector('.card-face')?.getAttribute('data-variant')).toBe('dark');
});
