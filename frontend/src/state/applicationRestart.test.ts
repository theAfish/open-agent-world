// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import * as profile from './profileStorage';
import { restartApplication } from './applicationRestart';

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

it('saves preferences and waits through shutdown and the old instance before refreshing', async () => {
  vi.useFakeTimers();
  const flush = vi.spyOn(profile, 'flushPreferences').mockResolvedValue();
  const restart = vi.spyOn(worldApi, 'restartApplication').mockResolvedValue({ instance_id: 'old' });
  const fetch = vi.spyOn(profile, 'fetchProfile')
    .mockResolvedValueOnce({ instance_id: 'old' } as profile.ApplicationProfile)
    .mockRejectedValueOnce(new Error('Connection closed'))
    .mockResolvedValue({ instance_id: 'new' } as profile.ApplicationProfile);
  const reload = vi.fn();
  const pending = restartApplication(reload);
  await vi.advanceTimersByTimeAsync(2000);
  expect(reload).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1000);
  await pending;
  expect(flush.mock.invocationCallOrder[0]).toBeLessThan(restart.mock.invocationCallOrder[0]);
  expect(fetch).toHaveBeenCalledTimes(3);
  expect(reload).toHaveBeenCalledOnce();
});

it('keeps the app running when preferences cannot be saved', async () => {
  vi.spyOn(profile, 'flushPreferences').mockRejectedValue(new Error('Save failed'));
  const restart = vi.spyOn(worldApi, 'restartApplication');
  await expect(restartApplication(vi.fn())).rejects.toThrow('Save failed');
  expect(restart).not.toHaveBeenCalled();
});

it('reports a failed recovery without refreshing the old instance', async () => {
  vi.useFakeTimers();
  vi.spyOn(profile, 'flushPreferences').mockResolvedValue();
  vi.spyOn(worldApi, 'restartApplication').mockResolvedValue({ instance_id: 'old' });
  vi.spyOn(profile, 'fetchProfile').mockRejectedValue(new Error('Offline'));
  const reload = vi.fn();
  const check = expect(restartApplication(reload)).rejects.toThrow('OAW has not restarted yet.');
  await vi.advanceTimersByTimeAsync(120_000);
  await check;
  expect(reload).not.toHaveBeenCalled();
});
