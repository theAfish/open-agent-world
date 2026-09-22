import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useState, type CSSProperties, type DragEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { EyeOff, GripVertical } from 'lucide-react';
import { t } from '../i18n';
import { useWorkspaceAccess } from './WorkspaceAccess';
import './workspaceSection.css';

export interface WorkspaceSectionRegistration {
  id: string;
  title: string;
  host: HTMLDivElement;
}

interface WorkspaceSectionContextValue {
  cardId: string;
  editing: boolean;
  detachedSectionIds: ReadonlySet<string>;
  hiddenSectionIds: ReadonlySet<string>;
  register: (section: WorkspaceSectionRegistration) => () => void;
  onSelect: (id: string) => void;
  onDragStart: (event: DragEvent, id: string) => void;
  onHide: (id: string) => void;
}

const WorkspaceSectionContext = createContext<WorkspaceSectionContextValue | null>(null);

export function WorkspaceSectionProvider({ children, ...value }: WorkspaceSectionContextValue & { children: ReactNode }) {
  return <WorkspaceSectionContext.Provider value={value}>{children}</WorkspaceSectionContext.Provider>;
}

/** Presentation only: card data and component state remain with the owning card. */
export function useWorkspaceSections(): { isInline: (id: string) => boolean } {
  const context = useContext(WorkspaceSectionContext);
  const detached = context?.detachedSectionIds;
  const hidden = context?.hiddenSectionIds;
  const access = useWorkspaceAccess();
  const allowed = context && access.plugin_access?.[context.cardId] ? access.permissions[context.cardId] : undefined;
  const isInline = useCallback((id: string) => (allowed === undefined || allowed.includes(id)) && !detached?.has(id) && !hidden?.has(id), [detached, hidden, allowed]);
  return useMemo(() => ({ isInline }), [isInline]);
}

export interface WorkspaceSectionProps {
  id: string;
  title: string;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}

/** A semantic card pane whose DOM can move without changing its React owner. */
export function WorkspaceSection({ id, title, children, className, style }: WorkspaceSectionProps) {
  const context = useContext(WorkspaceSectionContext);
  const access = useWorkspaceAccess();
  const permitted = !context || !access.plugin_access?.[context.cardId] || access.permissions[context.cardId]?.includes(id);
  const { isInline } = useWorkspaceSections();
  const inline = isInline(id);
  const [contentReady, setContentReady] = useState(inline);
  const [host] = useState(() => {
    const element = document.createElement('div');
    element.className = 'workspace-section-host';
    return element;
  });
  const attachInline = useCallback((element: HTMLDivElement | null) => {
    if (element) element.append(host);
  }, [host]);
  const register = context?.register;

  useLayoutEffect(() => {
    if (!permitted) return;
    host.dataset.workspaceSectionContent = id;
    const unregister = register?.({ id, title, host });
    // Initially detached panes need one registration commit so their destination
    // can attach the host before children measure it in their layout effects.
    setContentReady(true);
    return unregister;
  }, [host, id, title, register, permitted]);

  // Keep this portal at the same React position, including while hidden. Only its
  // DOM host moves; rendering another copy in the destination would reset state.
  if (!permitted) return null;
  return <>
    {inline && <div ref={attachInline} className={['workspace-section', context?.editing && 'is-editing', className].filter(Boolean).join(' ')}
      style={style} data-workspace-section={id} data-workspace-card={context?.cardId}>
      {context?.editing && <div className="workspace-section-controls" onPointerDown={event => event.stopPropagation()}>
        <button type="button" className="workspace-section-arrange" draggable
          aria-label={t('Arrange {v0}', { v0: title })} title={t('Arrange {v0}', { v0: title })}
          onClick={event => { event.stopPropagation(); context.onSelect(id); }}
          onDragStart={event => { event.stopPropagation(); context.onDragStart(event, id); }}>
          <GripVertical size={14} /><span>{title}</span>
        </button>
        <button type="button" className="workspace-section-hide" aria-label={t('Hide {v0}', { v0: title })}
          title={t('Hide {v0}', { v0: title })} onClick={event => { event.stopPropagation(); context.onHide(id); }}>
          <EyeOff size={14} />
        </button>
      </div>}
    </div>}
    {contentReady && createPortal(children, host)}
  </>;
}
