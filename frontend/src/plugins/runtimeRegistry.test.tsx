// @vitest-environment jsdom
import * as React from 'react';
import { Suspense, Component, useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { createViewRegistry, loadRuntimePlugin } from './registry';
import { providePackRuntime } from './sharedRuntime';
import type { FrontendPlugin, PluginViewProps } from './sdk';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const runtime = { api_version: 1, version: '0.1.0', url: '/api/packs/test.greeter/versions/0.1.0/frontend/index.js' };
const props = {} as PluginViewProps;
class Boundary extends Component<{ children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <p role="alert">Pack unavailable</p> : this.props.children; }
}

it('loads installed frontend with the exact host React and SDK, cached by immutable version', async () => {
  const host = providePackRuntime() as { react: typeof React; sdk: { t: unknown } };
  expect(host.react.useState).toBe(useState);
  expect(typeof host.sdk.t).toBe('function');
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

it.each(['missing module', 'missing view', 'wrong api', 'ownership collision'])('isolates %s to the Pack view', async reason => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const load = reason === 'missing module' ? vi.fn().mockRejectedValue(new Error('404'))
    : vi.fn().mockResolvedValue({ default: { apiVersion: reason === 'wrong api' ? 2 : 1, views: {} } });
  const sources = new Map();
  if (reason === 'ownership collision') sources.set('test.greeter', load);
  const View = createViewRegistry(sources, load)('test.greeter', 'greeting', runtime);
  render(<><button>Host control</button><Boundary><Suspense><View {...props} /></Suspense></Boundary></>);
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.getByText('Host control')).toBeTruthy();
});

it.each(['https://evil/index.js', '/api/packs/other/versions/0.1.0/frontend/index.js', '/api/packs/test.greeter/versions/0.1.0/frontend/../backend/code.js'])('rejects untrusted location %s', async url => {
  await expect(loadRuntimePlugin('test.greeter', { ...runtime, url })).rejects.toThrow('Invalid installed frontend location');
});
