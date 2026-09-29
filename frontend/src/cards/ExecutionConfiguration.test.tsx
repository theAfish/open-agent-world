// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ExecutionConfigurationBody } from './ExecutionConfiguration';
import { worldApi } from '../api/client';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { buildCardDraft } from '../state/helpers';

const card = { id: 'target-draft', ...buildCardDraft('execution.target', { x: 0, y: 0 }) };
beforeEach(() => {
  useNodeSurfaceStore.setState({ drafts: {}, privateDrafts: {} });
  vi.spyOn(worldApi, 'getNodeDocument').mockResolvedValue({ value: { name: 'Saved', provider_id: 'local', config: {} }, revision: 1, summary: {} });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('retains edits and their revision through hydration, then applies an explicit reload', async () => {
  const view = render(<ExecutionConfigurationBody card={card} />);
  await waitFor(() => expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Saved'));
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Unsaved' } });
  view.unmount();
  vi.mocked(worldApi.getNodeDocument).mockResolvedValue({ value: { name: 'Remote', provider_id: 'remote', config: {} }, revision: 2, summary: {} });
  render(<ExecutionConfigurationBody card={card} />);
  expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Unsaved');
  const save = vi.spyOn(worldApi, 'nodeDocumentAction').mockRejectedValue(new Error('Revision conflict'));
  fireEvent.click(screen.getByRole('button', { name: 'Save', exact: true }));
  await screen.findByText('Revision conflict');
  expect(save).toHaveBeenCalledWith(card.id, 'replace', { name: 'Unsaved', provider_id: 'local', config: {} }, 1);
  fireEvent.click(screen.getByRole('button', { name: 'Reload', exact: true }));
  await waitFor(() => expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Remote'));
});

it('refreshes a clean model when its view is hydrated again', async () => {
  const view = render(<ExecutionConfigurationBody card={card} />);
  await waitFor(() => expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Saved'));
  view.unmount();
  vi.mocked(worldApi.getNodeDocument).mockResolvedValue({ value: { name: 'Remote', provider_id: 'local', config: {} }, revision: 2, summary: {} });
  render(<ExecutionConfigurationBody card={card} />);
  await waitFor(() => expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Remote'));
});
