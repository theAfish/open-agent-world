export interface TransferProgress { loaded: number; total: number }
export interface TransferOptions { signal?: AbortSignal; onProgress?: (progress: TransferProgress) => void }
export interface TransferTask {
  name: string; size: number; direction: 'upload' | 'download'; scope?: string;
  run: (options: TransferOptions) => Promise<void>;
}
export interface TransferItem extends TransferProgress {
  id: number; name: string; direction: TransferTask['direction']; scope?: string;
  status: 'queued' | 'transferring' | 'succeeded' | 'failed' | 'cancelled'; error?: string;
}

/** One bounded scheduler for uploads and downloads. Cancelled work never auto-retries. */
export class TransferQueue {
  private next = 0;
  private listeners = new Set<() => void>();
  private tasks = new Map<number, { task: TransferTask; controller?: AbortController; settle: () => void }>();
  private items: TransferItem[] = [];
  private running = false;
  private disposed = false;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  snapshot = () => this.items;
  private update(id: number, patch: Partial<TransferItem>) {
    this.items = this.items.map(item => item.id === id ? { ...item, ...patch } : item);
    this.listeners.forEach(listener => listener());
  }
  add(tasks: TransferTask[]): Promise<void> {
    if (this.disposed) return Promise.resolve();
    const waits = tasks.map(task => new Promise<void>(settle => {
      const id = ++this.next;
      this.tasks.set(id, { task, settle });
      this.items = [...this.items, { id, name: task.name, direction: task.direction, scope: task.scope,
        loaded: 0, total: task.size, status: 'queued' }];
    }));
    this.listeners.forEach(listener => listener());
    void this.pump();
    return Promise.all(waits).then(() => undefined);
  }
  cancel = (id: number) => {
    const job = this.tasks.get(id), item = this.items.find(item => item.id === id);
    if (!job || !item || !['queued', 'transferring'].includes(item.status)) return;
    job.controller?.abort();
    this.update(id, { status: 'cancelled' }); job.settle();
  };
  retry = (id: number) => {
    const item = this.items.find(item => item.id === id), job = this.tasks.get(id);
    if (this.disposed || !job || !item || !['cancelled', 'failed'].includes(item.status)) return;
    this.update(id, { status: 'queued', loaded: 0, error: undefined }); void this.pump();
  };
  dismiss = (id: number) => {
    if (this.items.some(item => item.id === id && ['queued', 'transferring'].includes(item.status))) return;
    this.tasks.delete(id); this.items = this.items.filter(item => item.id !== id);
    this.listeners.forEach(listener => listener());
  };
  dispose() {
    this.disposed = true;
    this.items.forEach(item => this.cancel(item.id));
    this.tasks.clear();
  }
  private async pump() {
    if (this.running || this.disposed) return;
    this.running = true;
    try {
      for (;;) {
        const item = this.items.find(item => item.status === 'queued');
        if (!item || this.disposed) break;
        const job = this.tasks.get(item.id)!;
        const controller = new AbortController(); job.controller = controller;
        this.update(item.id, { status: 'transferring' });
        try {
          await job.task.run({ signal: controller.signal, onProgress: progress => {
            if (!controller.signal.aborted) this.update(item.id, progress);
          } });
          if (!controller.signal.aborted) {
            this.update(item.id, { status: 'succeeded', loaded: this.items.find(value => value.id === item.id)?.total ?? item.total });
            this.tasks.delete(item.id); // Release File objects and callbacks after completion.
          }
        } catch (reason) {
          if (!controller.signal.aborted) this.update(item.id, { status: 'failed', error: reason instanceof Error ? reason.message : String(reason) });
        } finally { job.controller = undefined; job.settle(); }
      }
    } finally { this.running = false; }
  }
}

export function saveDownload(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = name; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
