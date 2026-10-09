// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { droppedEntries, pickerEntries, validateEntries } from './fileIntake';

const limits = { maxEntries: 1000, maxFileBytes: 16 * 1024 * 1024, directories: true };
function file(name: string): FileSystemEntry {
  return { name, isFile: true, isDirectory: false, file: (resolve: (value: File) => void) => resolve(new File(['data'], name)) } as unknown as FileSystemEntry;
}
function folder(name: string, batches: FileSystemEntry[][]): FileSystemEntry {
  return { name, isDirectory: true, isFile: false, createReader: () => {
    let index = 0;
    return { readEntries: (resolve: (value: FileSystemEntry[]) => void) => resolve(batches[index++] ?? []) };
  } } as unknown as FileSystemEntry;
}
function transfer(entry: FileSystemEntry): DataTransfer {
  return { items: [{ kind: 'file', webkitGetAsEntry: () => entry, getAsFile: () => null }], files: [] } as unknown as DataTransfer;
}

describe('file intake', () => {
  it('reads every directory batch, retaining nested paths and empty folders', async () => {
    const entries = await droppedEntries(transfer(folder('project', [
      [file('one.txt'), folder('empty', [])], [folder('nested', [[file('two.txt')]])],
    ])), limits);
    expect(entries.map(entry => entry.path)).toEqual(['project', 'project/one.txt', 'project/empty', 'project/nested', 'project/nested/two.txt']);
    expect(entries[1].file?.size).toBe(4);
    expect(entries[2].file).toBeUndefined();
  });
  it('captures all drag handles before asynchronous traversal invalidates the data store', async () => {
    const read = vi.fn().mockReturnValue(file('second.txt'));
    const data = transfer(folder('first', [[file('one.txt')]]));
    Object.defineProperty(data, 'items', { value: [...Array.from(data.items), { kind: 'file', webkitGetAsEntry: read, getAsFile: () => null }] });
    const pending = droppedEntries(data, limits);
    expect(read).toHaveBeenCalledOnce();
    expect((await pending).at(-1)?.path).toBe('second.txt');
  });
  it('falls back to files and keeps directory picker paths', async () => {
    const input = new File(['data'], 'report.txt');
    Object.defineProperty(input, 'webkitRelativePath', { value: 'folder/report.txt' });
    expect(pickerEntries([input])[0].path).toBe('folder/report.txt');
    expect(await droppedEntries({ files: [input] } as unknown as DataTransfer, limits)).toEqual(pickerEntries([input]));
  });
  it('rejects unsupported directories, duplicates, traversal and oversized batches before upload', async () => {
    await expect(droppedEntries(transfer(folder('project', [])), { ...limits, directories: false })).rejects.toThrow('individual files');
    await expect(droppedEntries(transfer(folder('project', [[file('a'), file('b')]])), { ...limits, maxEntries: 2 })).rejects.toThrow('at most 2');
    for (const path of ['../escape', '/root', 'C:/host', 'a\\b', 'a//b']) {
      expect(() => validateEntries([{ path }], limits)).toThrow('Invalid relative');
    }
    expect(() => validateEntries([{ path: 'same' }, { path: 'same' }], limits)).toThrow('Duplicate');
    expect(() => validateEntries([{ path: 'large', file: new File(['data'], 'large') }], { ...limits, maxFileBytes: 3 })).toThrow('exceeds');
  });
});
