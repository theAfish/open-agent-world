// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';

afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); localStorage.clear(); });

it.each(['broken json', 'null', '{"textSize":"large"}', '{"textSize":1e999}'])(
  'uses a readable default for invalid saved appearance: %s', async saved => {
    vi.resetModules();
    localStorage.setItem('oaw-appearance', saved);
    const { useAppearance } = await import('./appearance');
    expect(useAppearance.getState().textSize).toBe(100);
  },
);

it('hydrates the active profile and persists bounded changes without reading another profile’s browser settings', async () => {
  vi.resetModules(); vi.useFakeTimers();
  localStorage.setItem('oaw-appearance', '{"textSize":80}');
  const { configureProfile, currentProfile, discardProfile } = await import('./profileStorage');
  configureProfile({ mode: 'development', profile_id: 'dev', generation: 'g1', version: '1',
    values: { 'oaw-appearance': '{"textSize":150,"wheelShortcut":false}' } });
  const { useAppearance } = await import('./appearance');
  expect(useAppearance.getState()).toMatchObject({ textSize: 150, wheelShortcut: false });
  useAppearance.getState().setTextSize(999);
  expect(useAppearance.getState().textSize).toBe(200);
  useAppearance.getState().setTextSize(-1);
  expect(useAppearance.getState().textSize).toBe(80);
  useAppearance.getState().setTextSize(134);
  useAppearance.getState().setWheelShortcut(true);
  expect(JSON.parse(currentProfile()!.values['oaw-appearance'])).toEqual({ textSize: 130, wheelShortcut: true });
  expect(localStorage.getItem('oaw-appearance')).toBe('{"textSize":80}');
  discardProfile();
});

it('keeps appearance usable when browser storage is unavailable', async () => {
  vi.resetModules();
  const { profileStorage } = await import('./profileStorage');
  vi.spyOn(profileStorage, 'getItem').mockImplementation(() => { throw new Error('disabled'); });
  vi.spyOn(profileStorage, 'setItem').mockImplementation(() => { throw new Error('disabled'); });
  const { useAppearance } = await import('./appearance');
  useAppearance.getState().setTextSize(120);
  expect(useAppearance.getState().textSize).toBe(120);
});
