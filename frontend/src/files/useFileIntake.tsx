import { useRef, useState, type DragEvent, type ClipboardEvent } from 'react';
import { t } from '../i18n';
import { droppedEntries, hasFiles, pickerEntries, validateEntries, type IncomingEntry, type IntakeLimits } from './fileIntake';
import './fileIntake.css';

/** Owns drag recognition, asynchronous folder collection and duplicate submission. */
export function useFileIntake<T>({ disabled, limits, destination, onEntries, onError }: {
  disabled: boolean; limits: IntakeLimits;
  destination: (target: EventTarget | null) => T;
  onEntries: (entries: IncomingEntry[], target: T) => Promise<void>;
  onError: (reason: unknown) => void;
}) {
  const [hovering, setHovering] = useState(false);
  const [processing, setProcessing] = useState(false);
  const lock = useRef(false);
  const depth = useRef(0);
  async function accept(read: () => Promise<IncomingEntry[]> | IncomingEntry[], target: T) {
    if (disabled || lock.current) return;
    lock.current = true; setProcessing(true);
    try {
      const entries = await read();
      validateEntries(entries, limits);
      if (entries.length) await onEntries(entries, target);
    } catch (reason) { onError(reason); }
    finally { lock.current = false; setProcessing(false); }
  }
  function claim(event: DragEvent<HTMLElement>) {
    if (!hasFiles(event.dataTransfer)) return false;
    event.preventDefault(); event.stopPropagation();
    event.dataTransfer.dropEffect = disabled || lock.current ? 'none' : 'copy';
    return true;
  }
  return {
    hovering: hovering && !disabled && !processing, processing,
    pick: (files: FileList | File[], target: T) => accept(() => pickerEntries(files), target),
    pasteProps: {
      onPaste: (event: ClipboardEvent<HTMLElement>) => {
        if (!event.clipboardData.files.length || disabled || lock.current) return;
        event.preventDefault(); event.stopPropagation();
        const files = Array.from(event.clipboardData.files);
        try { void accept(() => pickerEntries(files), destination(event.target)); }
        catch (reason) { onError(reason); }
      },
    },
    dragProps: {
      onDragEnter: (event: DragEvent<HTMLElement>) => { if (claim(event)) { depth.current++; setHovering(true); } },
      onDragOver: (event: DragEvent<HTMLElement>) => { if (claim(event)) setHovering(true); },
      onDragLeave: (event: DragEvent<HTMLElement>) => {
        if (!hasFiles(event.dataTransfer)) return;
        event.stopPropagation();
        depth.current = Math.max(0, depth.current - 1);
        if (!depth.current) setHovering(false);
      },
      onDrop: (event: DragEvent<HTMLElement>) => {
        if (!claim(event)) return;
        depth.current = 0; setHovering(false);
        // accept invokes read synchronously, before returning to the browser.
        try { void accept(() => droppedEntries(event.dataTransfer, limits), destination(event.target)); }
        catch (reason) { onError(reason); }
      },
      onDragEnd: () => { depth.current = 0; setHovering(false); },
    },
  };
}

export function FileDropOverlay({ visible, children }: { visible: boolean; children: React.ReactNode }) {
  return visible ? <div className="file-drop-overlay" role="status"><span>{children || t('Drop files here')}</span></div> : null;
}
