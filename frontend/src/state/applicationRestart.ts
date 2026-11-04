import { worldApi } from '../api/client';
import { fetchProfile, flushPreferences } from './profileStorage';

export async function restartApplication(reload = () => window.location.reload()) {
  await flushPreferences();
  const previous = await worldApi.restartApplication();
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    await new Promise(resolve => window.setTimeout(resolve, 1000));
    try {
      const next = await fetchProfile();
      if (next.instance_id && next.instance_id !== previous.instance_id) {
        reload();
        return;
      }
    } catch { /* The server is unavailable while its process restarts. */ }
  }
  throw new Error('OAW has not restarted yet. Check the launcher log, then refresh this page.');
}
