// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ImageCardBody } from './ImageCard';
import { buildCardDraft } from '../state/helpers';
import { useWorldStore } from '../state/worldStore';
import type { WorldCard } from '../types/world';

const card: WorldCard = { id: 'image-import', ...buildCardDraft('image', { x: 0, y: 0 }) };
const uploadImage = useWorldStore.getState().uploadImage;
afterEach(() => { cleanup(); useWorldStore.setState({ uploadImage }); vi.restoreAllMocks(); });

it('imports a pasted screenshot and leaves ordinary text paste untouched', async () => {
  const upload = vi.fn().mockResolvedValue(true);
  useWorldStore.setState({ uploadImage: upload });
  render(<ImageCardBody card={card} level="workspace" />);
  const target = screen.getByRole('button', { name: 'Import image' });
  expect(fireEvent.paste(target, { clipboardData: { files: [], getData: () => 'normal text' } })).toBe(true);
  const file = new File(['png'], 'screenshot.png', { type: 'image/png' });
  fireEvent.paste(target, { clipboardData: { files: [file] } });
  await waitFor(() => expect(upload).toHaveBeenCalledWith(card.id, file));
});
it('rejects a non-image drop and cannot replace an imported image', async () => {
  const upload = vi.fn().mockResolvedValue(true);
  useWorldStore.setState({ uploadImage: upload });
  const view = render(<ImageCardBody card={card} level="workspace" />);
  fireEvent.drop(screen.getByRole('button', { name: 'Import image' }), { dataTransfer: { types: ['Files'], files: [new File(['data'], 'notes.txt')] } });
  expect((await screen.findByRole('alert')).textContent).toContain('Choose a PNG');
  expect(upload).not.toHaveBeenCalled();
  view.rerender(<ImageCardBody card={{ ...card, config: { ...card.config, revision: 1, preview_url: '/image.png' } }} level="workspace" />);
  fireEvent.drop(view.container.firstChild!, { dataTransfer: { types: ['Files'], files: [new File(['png'], 'new.png', { type: 'image/png' })] } });
  expect(upload).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: 'Import image' })).toBeNull();
});
