// @vitest-environment jsdom
import * as React from 'react';
import { Suspense, Component, useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { assertFrontendAvailable, createViewRegistry, loadFrontendPlugin, loadRuntimePlugin, runtimeFrontendUrl } from './registry';
import { providePackRuntime } from './sharedRuntime';
import type { FrontendPlugin, PluginViewProps } from './sdk';
import { NetworkMap, ApplicationSurface, WorkspaceEmptyState, WorkspacePanel, WorkspaceToolbar, composeWorkspace } from './sdk';
import { loadPackFrontendService } from './frontendServices';
import { useWorldStore } from '../state/worldStore';

const originalCatalog = useWorldStore.getState().catalog;
afterEach(() => { cleanup(); vi.restoreAllMocks(); useWorldStore.setState({ catalog: originalCatalog }); });
const runtime = { api_version: 1, version: '0.1.0', url: '/api/packs/test.greeter/versions/0.1.0/frontend/index.js' };
const props = {} as PluginViewProps;
class Boundary extends Component<{ children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <p role="alert">Pack unavailable</p> : this.props.children; }
}

it('loads installed frontend with the exact host React and SDK, cached by immutable version', async () => {
  const host = providePackRuntime() as { react: typeof React; sdk: typeof import('./sdk') };
  expect(host.react.useState).toBe(useState);
  expect(typeof host.sdk.t).toBe('function');
  expect(host.sdk.NetworkMap).toBe(NetworkMap);
  expect(host.sdk.ApplicationSurface).toBe(ApplicationSurface);
  expect(host.sdk.WorkspaceEmptyState).toBe(WorkspaceEmptyState);
  expect(host.sdk.WorkspacePanel).toBe(WorkspacePanel);
  expect(host.sdk.WorkspaceToolbar).toBe(WorkspaceToolbar);
  expect(host.sdk.composeWorkspace).toBe(composeWorkspace);
  const Greeting = () => { const [count, setCount] = host.react.useState(0); return <button onClick={() => setCount(count + 1)}>Greetings {count}</button>; };
  const load = vi.fn().mockResolvedValue({ default: { apiVersion: 1, views: { greeting: Greeting } } satisfies FrontendPlugin });
  const registry = createViewRegistry(new Map(), load);
  const View = registry('test.greeter', 'greeting', runtime);
  expect(registry('test.greeter', 'greeting', runtime)).toBe(View);
  expect(registry('test.greeter', 'greeting', { ...runtime, version: '0.2.0' })).not.toBe(View);
  render(<Suspense><View {...props} /></Suspense>);
  fireEvent.click(await screen.findByText('Greetings 0'));
  expect(screen.getByText('Greetings 1')).toBeTruthy();
  expect(load).toHaveBeenCalledWith('test.greeter', runtime);
});

it.each(['missing module', 'missing view', 'wrong api'])('isolates %s to the Pack view', async reason => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const load = reason === 'missing module' ? vi.fn().mockRejectedValue(new Error('404'))
    : vi.fn().mockResolvedValue({ default: { apiVersion: reason === 'wrong api' ? 2 : 1, views: {} } });
  const sources = new Map();
  const View = createViewRegistry(sources, load)('test.greeter', 'greeting', runtime);
  render(<><button>Host control</button><Boundary><Suspense><View {...props} /></Suspense></Boundary></>);
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.getByText('Host control')).toBeTruthy();
});

it('loads the active installed frontend even when a source artifact remains in the host build', async () => {
  const source = vi.fn().mockResolvedValue({ default: { apiVersion: 1, views: { greeting: () => <p>Stale source</p> } } });
  const installed = vi.fn().mockResolvedValue({ default: { apiVersion: 1, views: { greeting: () => <p>Installed Pack</p> } } });
  const View = createViewRegistry(new Map([['test.greeter', source]]), installed)('test.greeter', 'greeting', runtime);
  render(<Suspense><View {...props} /></Suspense>);
  expect(await screen.findByText('Installed Pack')).toBeTruthy();
  expect(installed).toHaveBeenCalledWith('test.greeter', runtime);
  expect(source).not.toHaveBeenCalled();
});

it('rejects a stale source view once its owner is provided by an installed module', () => {
  useWorldStore.setState({ catalog: { ...originalCatalog,
    plugins: [{ id: 'test.greeter', version: '0.1.0', plugin_api_version: '1.35', name: null, description: null }],
    enabled_plugin_ids: ['test.greeter'], frontend_modules: { 'test.greeter': runtime } } });
  expect(() => assertFrontendAvailable('test.greeter')).toThrow('Source frontend');
  expect(() => assertFrontendAvailable('test.greeter', runtime)).not.toThrow();
});

it.each(['https://evil/index.js', '/api/packs/other/versions/0.1.0/frontend/index.js', '/api/packs/test.greeter/versions/0.1.0/frontend/../backend/code.js'])('rejects untrusted location %s', async url => {
  await expect(loadRuntimePlugin('test.greeter', { ...runtime, url })).rejects.toThrow('Invalid installed frontend location');
});

it('loads an explicitly mapped distribution while keeping the runtime owner stable', () => {
  const module = { ...runtime, distribution_id: 'publisher.greeter', url: '/api/packs/publisher.greeter/versions/0.1.0/frontend/index.js' };
  expect(runtimeFrontendUrl('test.greeter', module)).toBe(module.url);
  expect(() => runtimeFrontendUrl('test.greeter', { ...module, url: runtime.url })).toThrow('Invalid installed frontend location');
});

it('does not execute retained source artifacts when their runtime owner is absent', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const load = vi.fn().mockResolvedValue({ default: { apiVersion: 1, views: { body: () => <p>Should not execute</p> } } });
  useWorldStore.setState({ catalog: { ...originalCatalog, plugins: [], enabled_plugin_ids: [] } });
  const View = createViewRegistry(new Map([['removed.source', load]]), loadRuntimePlugin, assertFrontendAvailable)('removed.source', 'body');
  render(<Boundary><Suspense><View {...props} /></Suspense></Boundary>);
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(load).not.toHaveBeenCalled();
  await expect(loadFrontendPlugin('science.structure-viewer')).rejects.toThrow('not loaded or enabled');
});

it('rejects disabled service providers while allowing enabled owners without visible node types', async () => {
  const owner = 'science.structure-viewer';
  const descriptor = { id: owner, version: '1.0.0', plugin_api_version: '1.35', name: 'Structure Viewer', description: '' };
  useWorldStore.setState({ catalog: { ...originalCatalog, plugins: [descriptor], node_types: [], enabled_plugin_ids: [] } });
  await expect(loadPackFrontendService(owner, 'structure-renderer')).rejects.toThrow('not loaded or enabled');
  useWorldStore.setState({ catalog: { ...useWorldStore.getState().catalog, enabled_plugin_ids: [owner] } });
  expect(() => assertFrontendAvailable(owner)).not.toThrow();
  useWorldStore.setState({ catalog: { ...useWorldStore.getState().catalog, plugins: [], enabled_plugin_ids: [] } });
  await expect(loadPackFrontendService(owner, 'structure-renderer')).rejects.toThrow('not loaded');
});

it('rechecks a cached view against live enrollment before rendering it again', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const owner = 'retained.source';
  useWorldStore.setState({ catalog: { ...originalCatalog, plugins: [{ id: owner, version: '1.0.0', plugin_api_version: '1.35', name: 'Retained', description: '' }], enabled_plugin_ids: [owner] } });
  const load = vi.fn().mockResolvedValue({ default: { apiVersion: 1, views: { body: () => <p>Active source view</p> } } });
  const View = createViewRegistry(new Map([[owner, load]]), loadRuntimePlugin, assertFrontendAvailable)(owner, 'body');
  const rendered = render(<Boundary><Suspense><View {...props} /></Suspense></Boundary>);
  expect(await screen.findByText('Active source view')).toBeTruthy();
  useWorldStore.setState({ catalog: { ...originalCatalog, plugins: [], enabled_plugin_ids: [] } });
  rendered.rerender(<Boundary><Suspense><View {...props} /></Suspense></Boundary>);
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(load).toHaveBeenCalledOnce();
});
