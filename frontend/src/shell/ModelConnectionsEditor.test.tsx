// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { ModelConnectionsEditor } from './ModelConnectionsEditor';
import { EMPTY_MODEL_CATALOG } from '../state/modelConnections';

vi.mock('../state/interactions', () => ({ reportInteraction: vi.fn() }));
afterEach(cleanup);

it('creates a TypeSafe Jev connection with its own protocol, model and credential variable', () => {
  function Editor() {
    const [catalog, setCatalog] = useState(EMPTY_MODEL_CATALOG);
    return <ModelConnectionsEditor value={catalog} saved={EMPTY_MODEL_CATALOG} busy={false} onChange={setCatalog}/>;
  }
  render(<Editor/>);
  fireEvent.change(screen.getByLabelText('New connection type'), {target:{value:'typesafe'}});
  fireEvent.click(screen.getByRole('button', {name:'Add connection'}));
  expect((screen.getByLabelText('API format') as HTMLSelectElement).value).toBe('typesafe');
  expect((screen.getByLabelText(/^Base URL/) as HTMLInputElement).value).toBe('https://api.typesafe.ai');
  expect((screen.getByLabelText('Model 1 ID') as HTMLInputElement).value).toBe('jev-1.13.0');
  expect((screen.getByLabelText('API key') as HTMLInputElement).value).toBe('');
  fireEvent.click(screen.getByRole('button', {name:'Advanced connection options'}));
  fireEvent.change(screen.getByLabelText('Authentication source'), {target:{value:'environment'}});
  expect((screen.getByLabelText('Backend environment variable') as HTMLInputElement).placeholder).toBe('TYPESAFE_API_KEY');
});
