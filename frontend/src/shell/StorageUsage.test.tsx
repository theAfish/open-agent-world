// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { StorageUsage } from './StorageUsage';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('scans only on request and confirms removal of older backups', async () => {
  const latest = { name: 'latest', path: 'D:/latest', created_at: '', size_bytes: 1, removable: false };
  const older = { ...latest, name: 'older', path: 'D:/older', removable: true };
  const usage = { categories: { user_data: 1, managed: 2, temporary: 3, backups: 2 }, reclaimable_bytes: 1, measured_at: '', backups: [latest, older] };
  const scan = vi.spyOn(worldApi, 'inspectStorage').mockResolvedValueOnce(usage).mockResolvedValue({ ...usage, backups: [latest] });
  const remove = vi.spyOn(worldApi, 'deleteUpdateBackup').mockResolvedValue({ removed: 'older' });
  render(<StorageUsage />);
  expect(scan).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Analyze storage' }));
  await screen.findByText('D:/older');
  expect(screen.getAllByRole('button', { name: 'Delete backup' })).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'Delete backup' }));
  expect(remove).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm deletion' }));
  await waitFor(() => expect(remove).toHaveBeenCalledWith('older'));
  await waitFor(() => expect(screen.queryByText('D:/older')).toBeNull());
  expect(screen.getByText('D:/latest')).toBeTruthy();
});
