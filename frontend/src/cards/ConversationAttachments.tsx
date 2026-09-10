import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { conversationAttachmentUrl } from "../api/client";
import type { ConversationAttachment } from "../types/world";
import "./conversationAttachments.css";

export function ConversationAttachments({ conversationId, sessionId, files }: {
  conversationId: string; sessionId: string; files: ConversationAttachment[];
}) {
  const [preview, setPreview] = useState<ConversationAttachment>();
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
      return <div className="conversation-attachment" key={`${file.version_id}/${file.path}`}>
        {isImage ? <button type="button" className="conversation-image-thumbnail" aria-label={`Preview ${file.name}`} onClick={() => setPreview(file)}>
          <img src={conversationAttachmentUrl(conversationId, sessionId, file, true)} alt={file.name} loading="lazy" />
        </button> : null}
        <a href={url} download={file.name}>{file.name}</a>
        <small>{file.size_bytes.toLocaleString()} bytes</small>
      </div>;
    })}
    {preview ? createPortal(<div className="conversation-image-overlay" onClick={() => setPreview(undefined)}>
      <div role="dialog" aria-modal="true" aria-label={`Preview ${preview.name}`} onClick={(event) => event.stopPropagation()}>
        <button ref={close} type="button" onClick={() => setPreview(undefined)}>Close preview</button>
        <img src={conversationAttachmentUrl(conversationId, sessionId, preview, true)} alt={preview.name} />
      </div>
    </div>, document.body) : null}
  </div>;
}
