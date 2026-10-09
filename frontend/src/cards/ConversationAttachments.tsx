import { t, useLocale } from "../i18n";
import { useCallback, useState } from "react";
import { conversationAttachmentUrl, worldApi } from "../api/client";
import { FilePreview } from '../files/FilePreview';
import type { ConversationAttachment } from "../types/world";
import { useOpenFiles } from "../state/openFiles";
import "./conversationAttachments.css";

export function ConversationAttachments({ conversationId, sessionId, files }: {
  conversationId: string; sessionId: string; files: ConversationAttachment[];
}) {
  useLocale();
  const [preview, setPreview] = useState<ConversationAttachment>();
  const opened = useOpenFiles(state => state.sources[conversationId]);
  const loadText = useCallback((signal: AbortSignal) => worldApi.previewConversationAttachment(conversationId, sessionId, preview!, signal), [conversationId, sessionId, preview]);
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
        <button type="button" className="conversation-file-open" aria-label={t("Open {v0}", { v0: String(file.name) })}
          aria-pressed={opened?.reference.kind === "conversation" && opened.reference.version_id === file.version_id && opened.reference.path === file.path}
          onClick={() => { open(); setPreview(file); }}>{file.name}</button>
        <a href={url} download={file.name} aria-label={t("Download {v0}", { v0: String(file.name) })}>{t("Download")}</a>
        <small>{file.size_bytes.toLocaleString(useLocale.getState().locale)} {t("bytes")}</small>
      </div>;
    })}
    {preview && <FilePreview name={preview.name} downloadUrl={conversationAttachmentUrl(conversationId, sessionId, preview)}
      imageUrl={['image/png', 'image/jpeg', 'image/gif', 'image/webp'].includes(preview.media_type) ? conversationAttachmentUrl(conversationId, sessionId, preview, true) : undefined}
      loadText={loadText} onClose={() => setPreview(undefined)} />}
  </div>;
}
