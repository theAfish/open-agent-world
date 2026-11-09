import { useEffect, useRef, useState } from 'react';
import type { PluginViewProps } from '@oaw/plugin-api';

type Host = PluginViewProps['host'];
type Entry = { id: string; formula: string; name: string; kind: string; peaks: number; structure?: boolean };
type Report = { added: number; replaced: number; attached?: number; rejected: { filename: string; error: string }[] };
type Managed = { count: number; sha256?: string; metadata?: { last_import?: Report } };

const kinds: Record<string, string> = { calculated: '计算', card_export: '卡片' };

export function ManagedCards({ host, managed, revision, busy, onDocument, onError }: {
  host: Host; managed?: Managed | null; revision: number; busy: boolean;
  onDocument: (value: unknown, revision: number) => void; onError: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [entries, setEntries] = useState<{ entries: Entry[]; total: number }>({ entries: [], total: 0 });
  const [working, setWorking] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const structures = useRef<HTMLInputElement>(null);
  const count = managed?.count ?? 0;
  const report = managed?.metadata?.last_import;

  useEffect(() => {
    if (!open) return;
    let active = true;
    void host.documentAction('entries', { query }).then(d => { if (active) setEntries(d.value as typeof entries); })
      .catch(e => { if (active) onError(String(e)); });
    return () => { active = false; };
  }, [host, open, query, count, managed?.sha256, onError]);

  const run = async (action: string, args: Record<string, unknown>) => {
    setWorking(true); onError('');
    try {
      const d = await host.documentAction(action, args, revision);
      onDocument(d.value, d.revision);
    } catch (e) { onError(String(e)); } finally { setWorking(false); }
  };
  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    const cards = await Promise.all([...files].map(async file => ({ filename: file.name, text: await file.text() })));
    await run('add_cards', { cards });
  };
  const uploadStructures = async (files: FileList | null) => {
    if (!files?.length) return;
    const cifs = await Promise.all([...files].map(async file => ({ filename: file.name, text: await file.text() })));
    await run('add_structures', { cifs });
  };

  return <section className="xrd-managed-cards" aria-busy={working}>
    <input ref={picker} hidden multiple type="file" accept=".txt,.csv,text/plain" aria-label="添加标准卡片文本"
      onChange={e => { void upload(e.target.files); e.target.value = ''; }} />
    <input ref={structures} hidden multiple type="file" accept=".cif,text/plain" aria-label="附加 CIF 结构"
      onChange={e => { void uploadStructures(e.target.files); e.target.value = ''; }} />
    <div className="xrd-managed-head">
      <button type="button" className="xrd-library-source" aria-expanded={open} disabled={busy || working} onClick={() => setOpen(!open)}>
        <span className="xrd-library-slot">自有</span>
        <span className="xrd-library-source-copy"><strong>卡片谱库</strong></span>
        <span className="xrd-library-source-count">{count.toLocaleString()}</span>
      </button>
      <button type="button" disabled={busy || working} onClick={() => picker.current?.click()}>{working ? '正在添加…' : '添加卡片 ＋'}</button>
      <button type="button" disabled={busy || working || !count} onClick={() => structures.current?.click()}>附加 CIF ＋</button>
    </div>
    {report && <small role="status">{report.attached ? `附加结构 ${report.attached}` : `新增 ${report.added} · 替换 ${report.replaced}`}{report.rejected.length ? ` · 失败 ${report.rejected.length}（${report.rejected[0].filename}：${report.rejected[0].error}）` : ''}</small>}
    {open && <div className="xrd-managed-list">
      <input type="search" value={query} placeholder="按编号、分子式或名称筛选" aria-label="筛选条目" onChange={e => setQuery(e.target.value)} />
      <ul>{entries.entries.map(entry => <li key={entry.id}>
        <span title={entry.name}>{entry.id} · {entry.formula}</span><small>{kinds[entry.kind] ?? entry.kind} · {entry.peaks} 峰{entry.structure ? ' · 有结构' : ''}</small>
        <button type="button" disabled={busy || working} aria-label={`删除 ${entry.id}`} onClick={() => void run('remove_entries', { ids: [entry.id] })}>删除</button>
      </li>)}</ul>
      {entries.total > entries.entries.length && <small>仅显示前 {entries.entries.length} 条，共 {entries.total} 条</small>}
    </div>}
  </section>;
}
