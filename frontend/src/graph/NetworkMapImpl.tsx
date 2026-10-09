import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { t, useLocale } from '../i18n';
import { MapEngine } from './MapEngine';
import { describeNode, nodeColor } from './model';
import type { Island, NetworkData, NetworkMapHandle, Point } from './types';
import './networkMap.css';

export interface NetworkMapProps {
  graphKey: string;
  data: NetworkData;
  selected?: string[];
  onSelectionChange?: (ids: string[]) => void;
  onSelect?: (id: string) => void;
  onExpand?: (id: string) => void;
  onContextMenu?: (id: string, clientPoint: Point) => void;
  label?: string;
  /** Server-backed search stays in the Pack; local search remains useful for large loaded maps. */
  search?: boolean;
}

const NetworkMapImpl = forwardRef<NetworkMapHandle, NetworkMapProps>((props, ref) => {
  useLocale();
  const canvas = useRef<HTMLDivElement>(null), engine = useRef<MapEngine>();
  const current = useRef(props); current.current = props;
  const [query, setQuery] = useState(''), [kind, setKind] = useState('');
  const [hover, setHover] = useState(''), [selected, setSelected] = useState<string[]>([]);
  const [islands, setIslands] = useState<Island[]>([]), [busy, setBusy] = useState(true);
  const [error, setError] = useState(''), [back, setBack] = useState(false), [topics, setTopics] = useState(false);
  const [retry, setRetry] = useState(0);
  const selectedIds = props.selected ?? selected;
  useImperativeHandle(ref, () => ({ fit: () => engine.current?.fit(), focus: ids => engine.current?.focus(ids), arrange: () => engine.current?.arrange() }), []);
  useEffect(() => {
    let instance: MapEngine;
    setError(''); setBusy(true); setBack(false);
    try {
      instance = new MapEngine(canvas.current!, props.graphKey, {
        select: (ids, inspect) => {
          setSelected(ids); current.current.onSelectionChange?.(ids);
          if (inspect && ids[0]) current.current.onSelect?.(ids[0]);
        },
        expand: id => current.current.onExpand?.(id),
        menu: (id, point) => current.current.onContextMenu?.(id, point),
        hover: setHover, history: setBack,
        layout: (next, loading, message) => { setIslands(next); setBusy(loading); setError(message ?? ''); },
      });
      engine.current = instance;
      instance.update(current.current.data);
      instance.setSelection(current.current.selected ?? selected);
    } catch (cause) { setError(`${t('Map unavailable')}: ${String(cause)}`); setBusy(false); }
    return () => { instance?.destroy(); engine.current = undefined; };
  }, [props.graphKey, retry]);
  useEffect(() => { engine.current?.update(props.data); }, [props.data]);
  useEffect(() => { engine.current?.setSelection(selectedIds, true); }, [selectedIds, props.data]);
  const kinds = useMemo(() => [...new Set(props.data.nodes.map(n => n.kind ?? '').filter(Boolean))].sort(), [props.data.nodes]);
  const matching = useMemo(() => {
    const text = query.trim().toLocaleLowerCase();
    return props.data.nodes.filter(n => (!kind || n.kind === kind) && (!text || [n.label, n.id, n.topic, ...(n.tags ?? [])].join(' ').toLocaleLowerCase().includes(text)));
  }, [props.data.nodes, query, kind]);
  useEffect(() => { engine.current?.setFilter(query.trim() || kind ? matching.map(n => n.id) : null); }, [matching, query, kind, props.graphKey, retry]);
  const active = props.data.nodes.find(n => n.id === (hover || selectedIds[0]));
  const focused = props.data.nodes.find(n => n.id === selectedIds[0]);
  const related = focused ? props.data.edges.filter(e => e.source === focused.id || e.target === focused.id) : [];
  const choose = (id: string) => {
    if (engine.current) engine.current.choose(id);
    else { setSelected([id]); props.onSelectionChange?.([id]); props.onSelect?.(id); }
    setQuery('');
  };
  return <section className="network-map nodrag nopan nowheel" aria-label={props.label ?? t('Knowledge map')}
    data-layout-state={busy ? 'running' : error ? 'error' : 'ready'} data-node-count={props.data.nodes.length}
    onPointerDown={e => e.stopPropagation()} onMouseDown={e => e.stopPropagation()} onClick={e => e.stopPropagation()}
    onDoubleClick={e => e.stopPropagation()} onWheel={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}>
    <div className="network-map-stage" ref={canvas} tabIndex={0} role="application" aria-label={t('Interactive relationship map')} />
    <div className="network-map-tools">
      <button type="button" aria-label={t('Back to previous focus')} disabled={!back} onClick={() => engine.current?.back()}>←</button>
      {props.search !== false && <input type="search" aria-label={t('Find in map')} placeholder={t('Find in map')} value={query} onChange={e => setQuery(e.target.value)} />}
      <select aria-label={t('Filter map by type')} value={kind} onChange={e => setKind(e.target.value)}><option value="">{t('All types')}</option>{kinds.map(k => <option key={k}>{k}</option>)}</select>
      <button type="button" aria-label={t('Knowledge topics')} aria-expanded={topics} onClick={() => setTopics(!topics)}>{t('Topics')} <small>{islands.length}</small></button>
    </div>
    {topics && <div className="network-topics" aria-label={t('Knowledge topics')}>
      {islands.map(island => <button type="button" key={island.id} onClick={() => { engine.current?.focus(island.members); setTopics(false); }}><span>{island.label}</span><small>{island.members.length}</small></button>)}
    </div>}
    {(query.trim() || (error && !engine.current)) && <div className="network-results" role="listbox" aria-label={t('Map search results')}>
      {matching.slice(0, 30).map(n => <button type="button" role="option" aria-selected={selectedIds.includes(n.id)} key={n.id} onClick={() => choose(n.id)}><i style={{ background: nodeColor(n.kind) }} /><span>{n.label}</span><small>{n.kind}</small></button>)}
      {!matching.length && <p>{t('No matches')}</p>}
      {matching.length > 30 && <small>{matching.length} {t('matches')}</small>}
    </div>}
    {busy && <div className="network-map-status" role="status">{t('Arranging knowledge…')}</div>}
    {error && <div className="network-map-error" role="alert"><p>{error}</p><button type="button" onClick={() => engine.current ? engine.current.arrange() : setRetry(n => n + 1)}>{t('Retry layout')}</button></div>}
    {!busy && !props.data.nodes.length && <div className="network-map-status">{t('No knowledge to display')}</div>}
    {active && <div className="network-map-caption"><i style={{ background: nodeColor(active.kind) }} /><span>{describeNode(active)}</span>
      {props.onExpand && focused?.id === active.id && <button type="button" onClick={() => props.onExpand?.(active.id)}>{t('Expand neighborhood')}</button>}</div>}
    {focused && !props.onSelect && <aside className="network-map-details" aria-label={t('Relationships')}><strong>{focused.label}</strong>
      {related.slice(0, 30).map(e => { const other = props.data.nodes.find(n => n.id === (e.source === focused.id ? e.target : e.source));
        return other && <button type="button" key={e.id} onClick={() => choose(other.id)}><small>{e.source === focused.id ? '→' : '←'} {e.label}</small><span>{other.label}</span></button>; })}
    </aside>}
    <div className="network-map-scale" aria-label={t('Map controls')}>
      <button type="button" aria-label={t('Zoom in')} onClick={() => engine.current?.zoom(.8)}>+</button>
      <button type="button" aria-label={t('Zoom out')} onClick={() => engine.current?.zoom(1.25)}>−</button>
      <button type="button" aria-label={t('Fit View')} onClick={() => engine.current?.fit()}>⌖</button>
    </div>
    <div className="network-map-key" aria-label={t('Knowledge type legend')}>{kinds.slice(0, 8).map(k => <span key={k}><i style={{ background: nodeColor(k) }} />{k}</span>)}</div>
  </section>;
});
NetworkMapImpl.displayName = 'NetworkMapImpl';
export default NetworkMapImpl;
