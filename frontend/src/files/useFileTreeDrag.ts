import { useEffect, useRef, useState, type DragEvent } from 'react';
import { hasFiles } from './fileIntake';
import type { useFileIntake } from './useFileIntake';

const FILE_TREE_DRAG = 'application/x-oaw-workspace-entry';
export interface TreeDragEntry { path: string; directory: boolean }

/** Resolve the folder under the pointer, including a file's containing folder. */
export function workspaceDropFolder(target: EventTarget | null): string | undefined {
  const row = target instanceof Element ? target.closest<HTMLElement>('[data-drop-root]') : null;
  if (row && (row.dataset.dropRoot !== 'workspace' || row.dataset.dropBlocked === 'true')) return undefined;
  return row?.dataset.dropPath ?? '';
}

/** One target model for external uploads and moves within this file tree. */
export function useFileTreeDrag({ disabled, intake, expand, move }: {
  disabled: boolean; intake: Pick<ReturnType<typeof useFileIntake>, 'dragProps' | 'processing'>;
  expand: (path: string) => void; move: (entry: TreeDragEntry, folder: string) => void;
}) {
  const [folder, setFolder] = useState<string>();
  const [source, setSource] = useState<TreeDragEntry>();
  const dragged = useRef<TreeDragEntry>();
  const latest = useRef({ expand, move }); latest.current = { expand, move };
  useEffect(() => {
    if (folder === undefined) return;
    const timer = window.setTimeout(() => latest.current.expand(folder), 650);
    return () => window.clearTimeout(timer);
  }, [folder]);
  const internal = (event: DragEvent<HTMLElement>) => Array.from(event.dataTransfer.types).includes(FILE_TREE_DRAG);
  function target(event: DragEvent<HTMLElement>) {
    if (disabled || (!internal(event) && intake.processing)) return undefined;
    const path = workspaceDropFolder(event.target);
    const entry = dragged.current;
    if (internal(event) && (!entry || path === entry.path || path?.startsWith(`${entry.path}/`)
      || path === entry.path.split('/').slice(0, -1).join('/'))) return undefined;
    return path;
  }
  function hover(event: DragEvent<HTMLElement>) {
    if (!internal(event) && !hasFiles(event.dataTransfer)) return;
    event.preventDefault(); event.stopPropagation();
    const next = target(event);
    event.dataTransfer.dropEffect = next === undefined ? 'none' : internal(event) ? 'move' : 'copy';
    setFolder(next);
  }
  return {
    folder, source,
    start(event: DragEvent<HTMLElement>, entry: TreeDragEntry) {
      if (disabled) { event.preventDefault(); return; }
      event.stopPropagation();
      dragged.current = entry; setSource(entry);
      event.dataTransfer.setData(FILE_TREE_DRAG, entry.path);
      event.dataTransfer.effectAllowed = 'move';
    },
    props: {
      onDragEnter(event: DragEvent<HTMLElement>) { intake.dragProps.onDragEnter(event); hover(event); },
      onDragOver(event: DragEvent<HTMLElement>) {
        intake.dragProps.onDragOver(event); hover(event);
        if (!internal(event) && !hasFiles(event.dataTransfer)) return;
        const scroll = event.currentTarget.querySelector<HTMLElement>('.sandbox-tree-scroll');
        if (scroll) {
          const rect = scroll.getBoundingClientRect();
          if (event.clientY < rect.top + 24) scroll.scrollTop -= 16;
          else if (event.clientY > rect.bottom - 24) scroll.scrollTop += 16;
        }
      },
      onDragLeave(event: DragEvent<HTMLElement>) {
        intake.dragProps.onDragLeave(event);
        if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setFolder(undefined);
      },
      onDrop(event: DragEvent<HTMLElement>) {
        if (!internal(event) && !hasFiles(event.dataTransfer)) return;
        const next = target(event), entry = dragged.current;
        event.preventDefault(); event.stopPropagation(); setFolder(undefined);
        if (internal(event)) {
          if (next !== undefined && entry) latest.current.move(entry, next);
          dragged.current = undefined; setSource(undefined);
        } else if (next !== undefined) intake.dragProps.onDrop(event);
        else intake.dragProps.onDragEnd();
      },
      onDragEnd() {
        dragged.current = undefined; setSource(undefined); setFolder(undefined); intake.dragProps.onDragEnd();
      },
    },
  };
}
