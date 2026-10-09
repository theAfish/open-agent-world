import { t } from '../i18n';

/** Browser file intake shared by pickers and drop targets. Paths stay relative. */
export interface IncomingEntry { path: string; file?: File }
export interface IntakeLimits { maxEntries: number; maxFileBytes: number; directories: boolean }

export function pickerEntries(files: FileList | File[]): IncomingEntry[] {
  return Array.from(files, file => ({ path: file.webkitRelativePath || file.name, file }));
}

export function validateEntries(entries: IncomingEntry[], limits: IntakeLimits) {
  if (entries.length > limits.maxEntries) throw new Error(t('Choose at most {count} entries.', { count: limits.maxEntries }));
  const paths = new Set<string>();
  for (const entry of entries) {
    if (!limits.directories && (!entry.file || entry.path.includes('/'))) throw new Error(t('Drop individual files here; folders belong in Sandbox Files.'));
    if (!entry.path || /[\\:\0]/.test(entry.path) || entry.path.split('/').some(part => !part || part === '.' || part === '..' || /[. ]$/.test(part))) {
      throw new Error(t('Invalid relative file path: {path}', { path: entry.path }));
    }
    if (paths.has(entry.path)) throw new Error(t('Duplicate file path: {path}', { path: entry.path }));
    paths.add(entry.path);
    if (entry.file && entry.file.size > limits.maxFileBytes) throw new Error(t('File exceeds {limit} MiB: {path}', { limit: limits.maxFileBytes / 1024 / 1024, path: entry.path }));
  }
}

export function hasFiles(transfer: DataTransfer) {
  return Array.from(transfer.types ?? []).includes('Files') || Array.from(transfer.items ?? []).some(item => item.kind === 'file');
}

export async function droppedEntries(transfer: DataTransfer, limits: IntakeLimits): Promise<IncomingEntry[]> {
  // Capture entries/files while the drop event still owns the data store.
  const items = Array.from(transfer.items ?? []).filter(item => item.kind === 'file')
    .map(item => ({ entry: item.webkitGetAsEntry?.(), file: item.getAsFile() }));
  const fallback = pickerEntries(transfer.files ?? []);
  const result: IncomingEntry[] = [];
  function add(value: IncomingEntry) {
    result.push(value);
    if (result.length > limits.maxEntries) throw new Error(t('Choose at most {count} entries.', { count: limits.maxEntries }));
  }
  async function visit(entry: FileSystemEntry, parent = '', depth = 0): Promise<void> {
    if (depth > 64) throw new Error(t('Folder nesting exceeds 64 levels.'));
    const path = parent + entry.name;
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject));
      add({ path, file });
    } else if (entry.isDirectory) {
      if (!limits.directories) throw new Error(t('Drop individual files here; folders belong in Sandbox Files.'));
      add({ path }); // Include empty directories.
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
        if (!batch.length) break;
        for (const child of batch) await visit(child, `${path}/`, depth + 1);
      }
    }
  }
  if (items.length) {
    for (const item of items) {
      if (item.entry) await visit(item.entry);
      else if (item.file) add({ path: item.file.name, file: item.file });
    }
  } else result.push(...fallback);
  validateEntries(result, limits);
  return result;
}
