// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AgentPreview } from './AgentPreview';
import { CardStaticPreview } from './CardStaticPreview';
import type { NodeTypeCatalogItem, WorldCard } from '../types/world';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each(['openai.codex.agent', 'theafish.deepseek-harness.agent', 'theafish.pi.agent'])('summarizes %s as project and setting tags without printing runtime internals', type => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  const card: WorldCard = { id: 'worker', type, name: 'Worker', status: 'idle', expanded: false,
    position: { x: 0, y: 0 }, size: { width: 300, height: 190 }, config: {
      model: 'default', workspace_path: 'D:\\research\\project', reasoning_effort: 'high', session_mode: 'continue',
      system_instruction: 'Internal system instruction for connected OAW tools.', runtime_provider_id: 'private.provider',
    } };
  const view = render(<AgentPreview card={card} />);
  expect(screen.getByText('project').closest('[title]')?.getAttribute('title')).toBe('D:\\research\\project');
  expect(screen.getByText('Default').closest('.agent-preview-tag')).toBeTruthy();
  expect(screen.getByText('Continue').closest('.agent-preview-tag')?.querySelector('svg')).toBeTruthy();
  expect(view.container.textContent).not.toContain('Internal system instruction');
  expect(view.container.textContent).not.toContain('private.provider');
  expect(view.container.querySelector('input,textarea,button')).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it('keeps the same readable summary in lightweight previews and reflects configured model, purpose and read-only access', () => {
  const card: WorldCard = { id: 'reviewer', type: 'vendor.worker', name: 'Reviewer', status: 'idle', expanded: false,
    position: { x: 0, y: 0 }, size: { width: 300, height: 190 }, config: {
      model: 'review-model', description: 'Review proposed changes.', session_mode: 'fresh', workspace_access: 'read_only',
      system_instruction: 'Do not print this instruction on the card.',
    } };
  const definition = { id: card.type, traits: ['core.agent'] } as NodeTypeCatalogItem;
  const view = render(<CardStaticPreview card={card} definition={definition} workspace={false} />);
  expect(screen.getByText('review-model')).toBeTruthy();
  expect(screen.getByText('Review proposed changes.')).toBeTruthy();
  expect(screen.getByText('New session')).toBeTruthy();
  expect(screen.getByText('Read only')).toBeTruthy();
  expect(view.container.textContent).not.toContain('Do not print this instruction');
  expect(view.container.querySelector('.agent-preview-project')).toBeNull();
});
