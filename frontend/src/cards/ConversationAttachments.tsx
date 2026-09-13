import { t, useLocale } from "../i18n";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { conversationAttachmentUrl } from "../api/client";
import type { ConversationAttachment } from "../types/world";
import { useOpenFiles } from "../state/openFiles";
import "./conversationAttachments.css";

export function ConversationAttachments({ conversationId, sessionId, files }: {
  conversationId: string; sessionId: string; files: ConversationAttachment[];
}) {
  useLocale();
  const [preview, setPreview] = useState<ConversationAttachment>();
  const opened = useOpenFiles(state => state.sources[conversationId]);
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!preview) return;
    const previous = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.stopPropagation(); setPreview(undefined); }
      if (event.key === "Tab") { event.preventDefault(); close.current?.focus(); }
    };
    document.addEventListener("keydown", keydown, true);
    return () => { document.removeEventListener("keydown", keydown, true); previous?.focus(); };
  }, [preview]);
  return <div className="conversation-attachments">
    {files.map((file) => {
      const url = conversationAttachmentUrl(conversationId, sessionId, file);
      const isImage = ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(file.media_type);
      const open = () => useOpenFiles.getState().open({ kind: "conversation", source_id: conversationId,
        session_id: sessionId, version_id: file.version_id, path: file.path }, file.name);
      return <div className="conversation-attachment" key={`${file.version_id}/${file.path}`}>
        {isImage ? <button type="button" className="conversation-image-thumbnail" aria-label={t("Preview {v0}", { v0: String(file.name) })} onClick={() => { open(); setPreview(file); }}>
          <img src={conversationAttachmentUrl(conversationId, sessionId, file, true)} alt={file.name} loading="lazy" />
        </button> : null}
        <button type="button" className="conversation-file-open" aria-label={t("Open {v0}", { v0: String(file.name) })} title={t("Open in connected viewers")}
          aria-pressed={opened?.reference.kind === "conversation" && opened.reference.version_id === file.version_id && opened.reference.path === file.path}
          onClick={open}>{file.name}</button>
        <a href={url} download={file.name} aria-label={t("Download {v0}", { v0: String(file.name) })}>{t("Download")}</a>
        <small>{file.size_bytes.toLocaleString(useLocale.getState().locale)} {t("bytes")}</small>
      </div>;
    })}
    {preview ? createPortal(<div className="conversation-image-overlay" onClick={() => setPreview(undefined)}>
      <div role="dialog" aria-modal="true" aria-label={t("Preview {v0}", { v0: String(preview.name) })} onClick={(event) => event.stopPropagation()}>
        <button ref={close} type="button" onClick={() => setPreview(undefined)}>{t("Close preview")}</button>
        <img src={conversationAttachmentUrl(conversationId, sessionId, preview, true)} alt={preview.name} />
      </div>
    </div>, document.body) : null}
  </div>;
}
