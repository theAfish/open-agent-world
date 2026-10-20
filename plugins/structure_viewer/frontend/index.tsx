import { WorkspaceEmptyState, t, useLocale } from "@oaw/plugin-api";
import { useEffect, useRef, useState } from "react";
import { useFileViewer, type FrontendPlugin, type PluginViewProps } from "@oaw/plugin-api";
import "./style.css";

const supported = (name: string) => /(?:\.(?:cif|mcif|poscar|vasp|xyz|extxyz|json)|(?:^|\/)(?:POSCAR|CONTCAR))$/i.test(name);

function Preview({ card }: PluginViewProps) {
  useLocale();
  const { file, sources } = useFileViewer(card.id);
  return <p className="structure-summary">{file?.name ?? (sources.length ? t("Open a structure file in a connected window") : t("Connect a Sandbox or Conversation"))}</p>;
}

function Viewer({ card, host, level }: PluginViewProps) {
  useLocale();
  const { file, sources, pinned, setPinned } = useFileViewer(card.id);
  const target = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const source = sources.find(item => item.id === file?.reference.source_id);
  const active = level === "inspector" || level === "workspace";
  // Only new open-file intent may reveal the viewer. Mount, refresh, reconnect
  // and pinned previews must not steal focus from the user's current tab.
  const observedSequence = useRef(file?.sequence ?? 0);
  const connections = sources.map(source => source.id).sort().join('\0');
  const previous = useRef({ connections, pinned });
  useEffect(() => {
    const controller = new AbortController();
    let dispose: (() => void) | undefined;
    setError(""); setStatus("");
    const following = previous.current.connections === connections && !previous.current.pinned && !pinned;
    previous.current = { connections, pinned };
    if (!active || !file || !target.current) return;
    const reveal = following && file.reveal !== false && file.sequence > observedSequence.current;
    observedSequence.current = Math.max(observedSequence.current, file.sequence);
    if (!supported(file.name)) { setError(t("This file is not a supported structure. Open CIF, POSCAR, XYZ, or structure JSON.")); return; }
    const container = target.current;
    setStatus(t("Loading structure…"));
    void (async () => {
      const content = await host.readFile(file.reference, controller.signal);
      if (controller.signal.aborted) return;
      const { renderStructure, parseStructure } = await import("./render");
      if (controller.signal.aborted) return;
      const structure = parseStructure(content);
      if (reveal && host.workspace?.activate()) {
        file.onReveal?.();
        // Let the host attach and size the activated pane before the 3D camera
        // observes its viewport. Parsing remains the gate for navigation.
        await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
      }
      if (controller.signal.aborted) return;
      dispose = renderStructure(container, content, message => { if (!controller.signal.aborted) setError(message); }, structure);
      setStatus("");
    })().catch(error => {
      if (!controller.signal.aborted) { setError(error instanceof Error ? error.message : String(error)); setStatus(""); }
    });
    return () => { controller.abort(); dispose?.(); };
  }, [file, host, reload, active, pinned, connections]);
  if (!active) return <p className="structure-summary">{file?.name ?? t("Connect a file source")}</p>;
  return <section className={`structure-viewer structure-viewer--${level}`} aria-label={t("Structure viewer")}>
    {file && <div className="structure-toolbar">
      <div><strong title={file.name}>{file.name}</strong>{source && <small title={source.name}>{source.name}</small>}</div>
      <button type="button" disabled={!file} aria-pressed={pinned} onClick={() => setPinned(!pinned)}>{pinned ? t("Pinned") : t("Following")}</button>
      <button type="button" disabled={!file} onClick={() => setReload(value => value + 1)}>{t("Reload")}</button>
    </div>}
    <div className="structure-stage nodrag nopan nowheel">
      <div ref={target} className="structure-canvas" />
      {!file && <WorkspaceEmptyState className="structure-empty" title={sources.length ? t("Open a structure file in a connected window") : t("Connect this card to a Sandbox or Conversation")} description="CIF · POSCAR · XYZ · JSON" />}
      {status && <WorkspaceEmptyState className="structure-empty" busy title={status} />}
      {error && <div className="structure-empty" role="alert"><WorkspaceEmptyState title={error} actions={<button type="button" onClick={() => setReload(value => value + 1)}>{t("Reload")}</button>} /></div>}
    </div>
  </section>;
}

export default { apiVersion: 1, views: { preview: Preview, viewer: Viewer }, services: {
  'structure-renderer': {
    async prepare(file: { name: string; data: string }) { return (await import('./prepareStructure')).prepareStructure(file); },
    async render(target: HTMLElement, file: { name: string; data: string }, onError: (message: string) => void, prepared?: unknown) {
      const { renderStructure } = await import('./render');
      return renderStructure(target, file, onError, prepared as Parameters<typeof renderStructure>[3]);
    },
  },
} } satisfies FrontendPlugin;
