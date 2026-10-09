import { useEffect, useId, useRef, useState, type Ref, type MouseEvent, type KeyboardEvent } from 'react';
import { ChevronDown, ChevronRight, Copy, Download, File, FileText, Folder, FolderOpen, RefreshCw, Upload, FolderUp, Eye, Search, Trash2, X } from 'lucide-react';
import { worldApi, apiErrorMessage } from '../api/client';
import { IconButton } from '../components/IconButton';
import { ContextMenu, type ContextMenuAnchor } from '../components/ContextMenu';
import { useFileIntake } from '../files/useFileIntake';
import { useFileTreeDrag, workspaceDropFolder } from '../files/useFileTreeDrag';
import { ConfirmDialog, type Confirmation } from '../components/ConfirmDialog';
import { useHydrationLease } from '../canvas/useCardRendering';
import { TransferList, type useTransferQueue } from '../files/TransferList';
import { t, useLocale } from '../i18n';

export interface FileRoot { id: string; label: string; access: string; directory: boolean }
export interface FileEntry { name: string; path?: string; directory: boolean; blocked: boolean; size: number }
export interface FileListing { entries?: FileEntry[]; truncated?: boolean; next_cursor?: string; state?: string; message?: string }
export interface FileSelection { root: string; path: string; label: string; size?: number }
export interface FileChange { path: string; destination?: string }
const limits = { maxEntries: 1000, maxFileBytes: 1024 * 1024 * 1024, directories: true };

/** File interactions are independent of the terminal and preview presentation. */
export function SandboxFileBrowser({ cardId, roots, tree, expanded, loading, selection, error, workspacePath,
  writable, sidebarRef, onExpand, onSelect, onDownload, onRefresh, onLoadMore, onChange, transfers }: {
  cardId: string; roots: FileRoot[]; tree: Record<string, FileListing>; expanded: Record<string, boolean>;
  loading: Record<string, boolean>; selection?: FileSelection; error: string; workspacePath: string;
  writable: boolean; sidebarRef: Ref<HTMLElement>;
  onExpand: (root: string, path: string) => Promise<void>;
  onSelect: (root: string, path: string, label: string) => Promise<void>;
  onDownload: (file: FileSelection) => Promise<void>; onRefresh: () => Promise<void>;
  onLoadMore: (root: string, path: string, cursor: string) => Promise<void>;
  onChange: (change: FileChange) => Promise<void>;
  transfers: ReturnType<typeof useTransferQueue>;
}) {
  useLocale();
  const fileInput = useRef<HTMLInputElement>(null), folderInput = useRef<HTMLInputElement>(null);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  const [failure, setFailure] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const searchId = useId();
  const searchInput = useRef<HTMLInputElement>(null), searchToggle = useRef<HTMLButtonElement>(null);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState<FileListing>();
  const [searching, setSearching] = useState(false);
  const [revision, setRevision] = useState(0);
  const [mutating, setMutating] = useState(false);
  const mutationLock = useRef(false);
  const [confirmation, setConfirmation] = useState<Confirmation>();
  const searchRequest = useRef(0);
  const [chosen, setChosen] = useState<Record<string, FileSelection>>({});
  const lastChosen = useRef<string>();
  const [menu, setMenu] = useState<{ anchor: ContextMenuAnchor; file: FileSelection; directory: boolean }>();
  useEffect(() => { if (searchOpen) searchInput.current?.focus(); }, [searchOpen]);
  function closeSearch() {
    setSearchOpen(false);
    setQuery('');
    searchToggle.current?.focus();
  }
  function destination(target: EventTarget | null) {
    const path = workspaceDropFolder(target);
    if (path === undefined) throw new Error(t('Uploads are only available in the writable workspace.'));
    return path;
  }
  const intake = useFileIntake({
    disabled: !writable || mutating, limits, destination,
    onError: reason => { if (live.current) setFailure(apiErrorMessage(reason)); },
    onEntries: async (entries, folder) => {
      setFailure('');
      await transfers.queue.add(entries.map(entry => {
        let attempt = 0;
        return { name: entry.path, size: entry.file?.size ?? 0, direction: 'upload',
          run: async options => {
            const retry = attempt++ > 0;
            await worldApi.uploadSandboxEntry(cardId, folder ? `${folder}/${entry.path}` : entry.path, entry.file, options);
            if (retry && live.current) await onRefresh();
          },
        };
      }));
      if (live.current) { await onRefresh(); setRevision(value => value + 1); }
    },
  });
  useHydrationLease(cardId, 'sandbox-file-upload', intake.processing);
  useHydrationLease(cardId, 'sandbox-file-mutation', mutating || !!confirmation);
  async function mutate(change: FileChange) {
    if (!writable || mutationLock.current) throw new Error(t('File operation is unavailable'));
    mutationLock.current = true; setMutating(true); setFailure('');
    try {
      if (change.destination !== undefined) await worldApi.moveSandboxFile(cardId, change.path, change.destination);
      else await worldApi.deleteSandboxFile(cardId, change.path);
      if (live.current) { setChosen({}); setRevision(value => value + 1); await onChange(change); }
    } catch (reason) {
      // Recursive deletion can stop at a file in use; reflect completed work.
      if (live.current) { setRevision(value => value + 1); await onRefresh(); }
      throw reason;
    } finally { mutationLock.current = false; if (live.current) setMutating(false); }
  }
  const drag = useFileTreeDrag({ disabled: !writable || mutating || !!confirmation, intake,
    expand: path => { if (!expanded[`workspace:${path}`]) void onExpand('workspace', path); },
    move: (entry, folder) => {
      const destination = [folder, entry.path.split('/').at(-1)!].filter(Boolean).join('/');
      void mutate({ path: entry.path, destination }).catch(reason => { if (live.current) setFailure(apiErrorMessage(reason)); });
    },
  });
  const pick = (files: FileList | null) => { if (files) void intake.pick(files, ''); };
  async function searchFiles(cursor = '') {
    const request = ++searchRequest.current;
    setSearching(true);
    try {
      const result = await worldApi.sandboxWorkspace<FileListing>(cardId, `files?${new URLSearchParams({ operation: 'list', root: 'workspace', query: query.trim(), cursor })}`);
      if (live.current && request === searchRequest.current) setSearch(previous => ({ ...result, entries: cursor ? [...(previous?.entries ?? []), ...(result.entries ?? [])] : result.entries }));
    } catch (reason) { if (live.current && request === searchRequest.current) setSearch({ state: 'error', message: apiErrorMessage(reason) }); }
    finally { if (live.current && request === searchRequest.current) setSearching(false); }
  }
  useEffect(() => {
    searchRequest.current++; setSearch(undefined); setChosen({}); setSearching(false);
    if (!query.trim()) return;
    const timer = window.setTimeout(() => void searchFiles(), 200);
    return () => { window.clearTimeout(timer); searchRequest.current++; };
  }, [query, revision]);
  const visibleFiles: FileSelection[] = [];
  function collect(root: string, path: string) {
    if (!expanded[`${root}:${path}`]) return;
    for (const entry of tree[`${root}:${path}`]?.entries ?? []) {
      const next = entry.path ?? (path ? `${path}/${entry.name}` : entry.name);
      if (entry.directory) collect(root, next);
      else if (!entry.blocked) visibleFiles.push({ root, path: next, label: entry.name, size: entry.size });
    }
  }
  if (query.trim()) {
    for (const entry of search?.entries ?? []) if (!entry.directory && !entry.blocked) visibleFiles.push({ root: 'workspace', path: entry.path!, label: entry.name, size: entry.size });
  } else for (const root of roots) { if (root.directory) collect(root.id, ''); else visibleFiles.push({ root: root.id, path: '', label: root.label }); }
  const identity = (file: FileSelection) => `${file.root}:${file.path}`;
  function choose(file: FileSelection, range = false) {
    const id = identity(file);
    const anchor = lastChosen.current;
    setChosen(previous => {
      const next = { ...previous };
      const from = visibleFiles.findIndex(item => identity(item) === anchor), to = visibleFiles.findIndex(item => identity(item) === id);
      if (range && from >= 0 && to >= 0) for (const entry of visibleFiles.slice(Math.min(from, to), Math.max(from, to) + 1)) next[identity(entry)] = entry;
      else if (next[id]) delete next[id]; else next[id] = file;
      return next;
    });
    lastChosen.current = id;
  }
  const selectedFiles = Object.values(chosen);
  const downloadChosen = () => { for (const file of selectedFiles) void onDownload(file); };
  function openMenu(event: MouseEvent<HTMLButtonElement> | KeyboardEvent<HTMLButtonElement>, file: FileSelection, directory: boolean) {
    event.preventDefault(); event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    setMenu({ file, directory, anchor: { trigger: event.currentTarget,
      x: 'clientX' in event ? event.clientX : rect.left + 12, y: 'clientY' in event ? event.clientY : rect.bottom } });
  }
  function fileActions(file: FileSelection, directory: boolean) {
    return {
      onContextMenu: (e: MouseEvent<HTMLButtonElement>) => openMenu(e, file, directory),
      onKeyDown: (e: KeyboardEvent<HTMLButtonElement>) => {
        if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) openMenu(e, file, directory);
      },
    };
  }
  function row(root: string, path: string, entry: FileEntry, searchResult = false): React.ReactNode {
    const next = entry.path ?? (path ? `${path}/${entry.name}` : entry.name);
    const file = { root, path: next, label: entry.name, size: entry.size };
    return <li key={next}>
      <div className={`sandbox-file-row${chosen[identity(file)] ? ' is-selected' : ''}`}
        data-drop-root={root} data-drop-path={entry.directory ? next : next.split('/').slice(0, -1).join('/')} data-drop-blocked={entry.blocked}
        data-drop-target={root === 'workspace' && entry.directory && drag.folder === next || undefined}
        data-drag-source={root === 'workspace' && drag.source?.path === next || undefined}>
        <button className="sandbox-tree-entry" disabled={entry.blocked} title={entry.blocked ? t('Links are blocked') : next}
          draggable={writable && !mutating && !intake.processing && root === 'workspace' && !entry.blocked}
          onDragStart={event => drag.start(event, { path: next, directory: entry.directory })}
          aria-expanded={entry.directory ? !!expanded[`${root}:${next}`] : undefined}
          aria-current={!entry.directory && selection?.root === root && selection.path === next ? 'true' : undefined}
          aria-pressed={!entry.directory ? !!chosen[identity(file)] : undefined}
          {...fileActions(file, entry.directory)} onClick={event => {
            if (!entry.directory && (event.ctrlKey || event.metaKey || event.shiftKey)) choose(file, event.shiftKey);
            else void (entry.directory ? onExpand(root, next) : onSelect(root, next, entry.name));
          }}>
          {entry.directory ? <>{expanded[`${root}:${next}`] ? <ChevronDown size={11} /> : <ChevronRight size={11} />}<Folder size={13} /></> : <FileText size={13} />}
          <span>{searchResult ? next : entry.name}</span>
        </button>
      </div>{entry.directory && directory(root, next)}
    </li>;
  }
  function directory(root: string, path: string): React.ReactNode {
    const key = `${root}:${path}`, value = tree[key];
    if (!expanded[key]) return null;
    return <ul>{loading[key] && !value ? <li className="sandbox-tree-note">{t('Loading…')}</li> : value?.state ? <li className="sandbox-tree-note">{value.message ?? value.state}</li> : <>
      {value?.entries?.length === 0 && <li className="sandbox-tree-note">{t('Empty folder')}</li>}
      {value?.entries?.map(entry => row(root, path, entry))}
      {value?.next_cursor && <li><button className="sandbox-tree-entry sandbox-load-more" disabled={loading[key]} onClick={() => void onLoadMore(root, path, value.next_cursor!)}>{t(loading[key] ? 'Loading…' : 'Load more')}</button></li>}
    </>}</ul>;
  }
  return <aside ref={sidebarRef} className="sandbox-files file-drop-target nodrag nopan nowheel" aria-label={t('Sandbox files')}
    aria-busy={mutating} data-drop-active={drag.folder !== undefined || undefined} {...drag.props}>
    <header className="sandbox-pane-heading"><span><FolderOpen size={13} /> {t('Files')}</span>
      <div className="sandbox-pane-actions">
        <IconButton ref={searchToggle} icon={Search} size="xs" quiet label={t('Search workspace files')}
          disabled={!roots.some(root => root.id === 'workspace')} aria-expanded={searchOpen} aria-controls={searchOpen ? searchId : undefined}
          onClick={() => { if (searchOpen) closeSearch(); else setSearchOpen(true); }} />
        {writable && <>
          <IconButton icon={Upload} size="xs" quiet label={t('Upload files')} disabled={intake.processing || mutating} onClick={() => fileInput.current?.click()} />
          <IconButton icon={FolderUp} size="xs" quiet label={t('Upload folder')} disabled={intake.processing || mutating} onClick={() => folderInput.current?.click()} />
        </>}
        <IconButton icon={RefreshCw} size="xs" quiet label={t('Refresh files')} disabled={loading.roots || searching} onClick={() => { void onRefresh(); if (query.trim()) void searchFiles(); }} />
      </div>
    </header>
    {searchOpen && <div id={searchId} className="sandbox-file-search" onKeyDown={event => {
      event.stopPropagation();
      if (event.key === 'Escape') { event.preventDefault(); closeSearch(); }
    }}><input ref={searchInput} type="search" value={query} aria-label={t('Search workspace files')} placeholder={t('Search files')} onChange={event => setQuery(event.target.value)} />
      {query && <IconButton icon={X} size="xs" quiet label={t('Clear search')} onClick={() => { setQuery(''); searchInput.current?.focus(); }} />}</div>}
    {selectedFiles.length > 0 && <div className="sandbox-file-selection">
      <input type="checkbox" aria-label={t('Select all visible files')} checked={visibleFiles.length > 0 && visibleFiles.every(file => !!chosen[identity(file)])}
        onChange={event => setChosen(event.target.checked ? Object.fromEntries(visibleFiles.map(file => [identity(file), file])) : {})} />
      <span>{t('{count} selected', { count: selectedFiles.length })}</span>
      <IconButton icon={Download} size="xs" quiet label={t('Download selected files')} onClick={downloadChosen} />
      <IconButton icon={X} size="xs" quiet label={t('Clear selection')} onClick={() => setChosen({})} />
    </div>}
    <input ref={fileInput} type="file" hidden multiple aria-label={t('Upload files')} onChange={e => { pick(e.target.files); e.target.value = ''; }} />
    <input ref={folderInput} type="file" hidden multiple {...{ webkitdirectory: '' }} aria-label={t('Upload folder')} onChange={e => { pick(e.target.files); e.target.value = ''; }} />
    <div className="sandbox-tree-scroll">
      {failure && <p className="sandbox-tree-note sandbox-upload-error" role="alert">{failure}</p>}
      {loading.roots && !roots.length && <p className="sandbox-tree-note">{t('Loading files…')}</p>}
      {error && <p className="sandbox-tree-note" role="alert">{error}</p>}
      {!loading.roots && !roots.length && !error && <p className="sandbox-tree-note">{t('Start the sandbox to browse files.')}</p>}
      {query.trim() ? <section className="sandbox-file-root"><ul>
        {searching && !search && <li role="status" className="sandbox-tree-note">{t('Searching…')}</li>}
        {search?.message && <li role="alert" className="sandbox-tree-note">{search.message}</li>}
        {search?.entries?.length === 0 && <li className="sandbox-tree-note">{t('No files found')}</li>}
        {search?.entries?.map(entry => row('workspace', '', entry, true))}
        {search?.next_cursor && <li><button className="sandbox-tree-entry sandbox-load-more" disabled={searching} onClick={() => void searchFiles(search.next_cursor)}>{t(searching ? 'Loading…' : 'Load more')}</button></li>}
      </ul></section> : roots.map(root => <section className="sandbox-file-root" key={root.id} data-drop-root={root.id} data-drop-path="">
        <button className="sandbox-tree-entry sandbox-root-entry" aria-expanded={root.directory ? !!expanded[`${root.id}:`] : undefined}
          data-drop-target={root.id === 'workspace' && drag.folder === '' || undefined}
          title={root.id === 'workspace' ? workspacePath : root.label}
          {...fileActions({ root: root.id, path: '', label: root.label }, root.directory)}
          onClick={() => void (root.directory ? onExpand(root.id, '') : onSelect(root.id, '', root.label))}>
          {root.directory ? <>{expanded[`${root.id}:`] ? <ChevronDown size={11} /> : <ChevronRight size={11} />}<Folder size={13} /></> : <File size={13} />}
          <span>{root.label}</span>{root.access === 'read_only' && <small>{t('Read only')}</small>}
        </button>
        {root.directory && directory(root.id, '')}
      </section>)}
    </div>
    <TransferList queue={transfers.queue} items={transfers.items} />
    <footer className="sandbox-files-footer" title={workspacePath}><Folder size={11} /><span>{workspacePath}</span></footer>
    {confirmation && <ConfirmDialog confirmation={confirmation} onClose={() => setConfirmation(undefined)} />}
    {menu && <ContextMenu anchor={menu.anchor} label={t('File actions')} onClose={() => setMenu(undefined)}>
      <button role="menuitem" onClick={() => void (menu.directory ? onExpand(menu.file.root, menu.file.path) : onSelect(menu.file.root, menu.file.path, menu.file.label))}><Eye size={13} />{menu.directory ? (expanded[`${menu.file.root}:${menu.file.path}`] ? t('Collapse folder') : t('Open folder')) : t('Preview')}</button>
      {!menu.directory && <button role="menuitem" onClick={() => void onDownload(menu.file)}><Download size={13} />{t('Download')}</button>}
      {selectedFiles.length > 1 && <button role="menuitem" onClick={downloadChosen}><Download size={13} />{t('Download selected files')}</button>}
      {writable && menu.file.root === 'workspace' && menu.file.path && <button role="menuitem" className="is-danger" disabled={mutating || intake.processing} onClick={() => {
        const file = menu.file;
        setConfirmation({ title: t('Delete {name}?', { name: file.label }), items: [file.path], action: t('Delete'),
          description: menu.directory ? t('This folder and its contents will be permanently deleted.') : undefined,
          run: () => mutate({ path: file.path }),
        });
      }}><Trash2 size={13} />{t('Delete')}</button>}
      <button role="menuitem" onClick={async () => {
        try { await navigator.clipboard.writeText(menu.file.path || (menu.file.root === 'workspace' ? '.' : menu.file.label)); }
        catch (reason) { setFailure(apiErrorMessage(reason)); }
      }}><Copy size={13} />{t('Copy relative path')}</button>
    </ContextMenu>}
  </aside>;
}
