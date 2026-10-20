// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { PackInstaller } from './PackInstaller';
import type { PackInstallations } from '../types/packs';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('reconnects a disconnected install to its durable operation without resubmitting', async () => {
  vi.spyOn(worldApi, 'inspectPack').mockResolvedValue({ manifest: { id: 'example.tools', name: 'Tools', version: '1.0.0' }, sha256: 'digest' });
  const install = vi.spyOn(worldApi, 'installPack').mockRejectedValue(new Error('Connection lost'));
  const accepted = vi.fn();
  const target = document.createElement('div');
  document.body.append(target);
  try {
    const props = { actionTarget: target, onInstalled: accepted };
    const view = render(<PackInstaller {...props} />);
    const file = new File(['zip'], 'tools.oawpack');
    vi.spyOn(file, 'slice').mockReturnValue({ arrayBuffer: async () => new Uint8Array([0x50, 0x4b, 3, 4]).buffer } as Blob);
    await act(async () => fireEvent.change(screen.getByLabelText('Pack file'), { target: { files: [file] } }));
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Install Pack' })));
    expect(install).toHaveBeenCalledOnce();
    const installations: PackInstallations = { restart_required: false, versions: [], operations: [{
      id: 'operation-1', digest: 'digest', pack_id: 'example.tools', version: '1.0.0', state: 'preparing', error: null, updated_at: 1,
    }] };
    view.rerender(<PackInstaller {...props} installations={installations} />);
    expect(screen.getByRole('status').textContent).toContain('Preparing environments');
    expect((screen.getByRole('button', { name: 'Installing' }) as HTMLButtonElement).disabled).toBe(true);
    view.rerender(<PackInstaller {...props} installations={{ ...installations, restart_required: true,
      versions: [{ id: 'example.tools', name: 'Tools', version: '1.0.0', selected: true, loaded: false, environment: null }],
      operations: [{ ...installations.operations![0], state: 'completed' }],
    }} />);
    expect((screen.getByRole('button', { name: 'Installed' }) as HTMLButtonElement).disabled).toBe(true);
    expect(install).toHaveBeenCalledOnce();
    expect(accepted).not.toHaveBeenCalled();
  } finally { target.remove(); }
});
