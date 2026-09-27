/** Shared user-facing vocabulary; provider states remain unchanged on the wire. */
export function runStatusLabel(status: string): string {
  return ({ created: 'Queued', queued: 'Queued', running: 'Working', waiting: 'Waiting',
    succeeded: 'Completed', completed: 'Completed', failed: 'Failed', cancelled: 'Stopped',
    interrupted: 'Interrupted' } as Record<string, string>)[status] ?? status;
}
