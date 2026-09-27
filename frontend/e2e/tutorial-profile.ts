import { expect, type APIRequestContext } from '@playwright/test';

/** Tutorial progress is owned by the test backend's profile, including on reload. */
export async function resetTutorialProfile(request: APIRequestContext, session?: Record<string, unknown>) {
  const profile = await (await request.get('/api/application')).json();
  const response = await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': session ? JSON.stringify({ version: 1, state: { status: 'started', session } }) : null,
      'oaw-canvas-viewport-v1': null, 'oaw-node-surfaces-v1': null, 'oaw-theme': null,
      'oaw-active-workspace-v1': null,
    },
  } });
  expect(response.ok()).toBe(true);
}
