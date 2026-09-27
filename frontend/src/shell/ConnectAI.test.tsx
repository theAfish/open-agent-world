// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { ConnectAI } from './ConnectAI';
import { EMPTY_MODEL_CATALOG } from '../state/modelConnections';
import { worldApi } from '../api/client';
import { useLocale } from '../i18n';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function Setup() {
  const [value, onChange] = useState(EMPTY_MODEL_CATALOG);
  return <><ConnectAI value={value} onChange={onChange} busy={false} onAdvanced={() => {}} /><output>{value.default_model}</output></>;
}

it('connects a provider and selects a returned model without typing an ID', async () => {
  useLocale.setState({ locale: 'en' });
  const discover = vi.spyOn(worldApi, 'discoverModels').mockResolvedValue({ models: [{ id: 'chat-latest', name: 'Chat model' }], truncated: false });
  render(<Setup />);
  fireEvent.click(screen.getByRole('button', { name: 'OpenAI' }));
  fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'private-key' } });
  fireEvent.click(screen.getByRole('button', { name: 'Get available models' }));
  fireEvent.change(await screen.findByLabelText('Available models'), { target: { value: 'chat-latest' } });
  expect(discover).toHaveBeenCalledWith(expect.objectContaining({ api_key: 'private-key', adapter: 'openai' }), expect.any(AbortSignal));
  expect(screen.getByText('Chat model', { selector: 'strong' })).toBeTruthy();
  expect(screen.getByText(/^oaw:model:/).textContent).toContain('oaw:model:');
});

it('ignores a stale response after switching providers', async () => {
  let complete!: (result: { models: { id: string; name: string }[]; truncated: boolean }) => void;
  const discover = vi.spyOn(worldApi, 'discoverModels').mockImplementation(() => new Promise(resolve => { complete = resolve; }));
  render(<Setup />);
  fireEvent.click(screen.getByRole('button', { name: 'Ollama' }));
  fireEvent.click(screen.getByRole('button', { name: 'Get available models' }));
  await waitFor(() => expect(discover).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('button', { name: 'Anthropic' }));
  await act(async () => complete({ models: [{ id: 'old-model', name: 'Old model' }], truncated: false }));
  expect(screen.queryByLabelText('Available models')).toBeNull();
  expect(discover.mock.calls[0][1]?.aborted).toBe(true);
});
