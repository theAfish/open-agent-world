import { useWorkspaceAccess } from '../workspace/WorkspaceAccess';
import { t, useLocale } from "../i18n";
import { ImagePlus, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import type { NodeSurfaceLevel } from "../state/nodeSurfaces";
import { RelationshipList } from "./CardUtilities";
import { useFileIntake, FileDropOverlay } from '../files/useFileIntake';
import { apiErrorMessage } from '../api/client';
import { useHydrationLease } from '../canvas/useCardRendering';

function formatBytes(bytes: unknown): string {
  if (typeof bytes !== "number" || !Number.isFinite(bytes)) return t("No file imported");
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ImageCardBody({ card }: { card: WorldCard; level: NodeSurfaceLevel }) {
  useLocale();
  const { deployed } = useWorkspaceAccess();
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadImage = useWorldStore((state) => state.uploadImage);
  const [error, setError] = useState('');
  const previewUrl = typeof card.config.preview_url === "string" ? card.config.preview_url : undefined;
  const filename = String(card.config.filename ?? card.name);
  const imported = Boolean(previewUrl || Number(card.config.revision ?? 0) > 0);

  const intake = useFileIntake({
    disabled: deployed || imported,
    limits: { maxEntries: 1, maxFileBytes: 25 * 1024 * 1024, directories: false },
    destination: () => undefined,
    onError: reason => setError(apiErrorMessage(reason)),
    onEntries: async entries => {
      const file = entries[0].file!;
      if (!/^image\/(png|jpeg|gif|webp)$/.test(file.type) && !/\.(png|jpe?g|gif|webp)$/i.test(file.name)) throw new Error(t('Choose a PNG, JPEG, GIF or WebP image.'));
      setError('');
      await uploadImage(card.id, file);
    },
  });
  const uploading = intake.processing;
  useHydrationLease(card.id, 'image-import', uploading);

  const preview = previewUrl ? (
    <img src={previewUrl} alt={t("Preview of {v0}", { v0: String(filename) })} draggable={false} />
  ) : (
    <div className="image-placeholder" aria-label={t("No image imported")}>
      <span><ImagePlus size={26} /></span>
      <i /><i /><i />
      <small>{t("Awaiting image")}</small>
    </div>
  );

  return (
    <div className="expanded-stack file-drop-target" {...intake.dragProps} {...intake.pasteProps}>
      <div className="image-preview-expanded">{preview}</div>
      <div className="image-metadata-grid">
        <div><span>{t("Filename")}</span><strong title={filename}>{filename}</strong></div>
        <div><span>{t("Dimensions")}</span><strong>{card.config.image_width && card.config.image_height
          ? `${card.config.image_width} × ${card.config.image_height}`
          : t("Not available")}</strong></div>
        <div><span>{t("Format")}</span><strong>{String(card.config.mime_type ?? t("Unknown"))}</strong></div>
        <div><span>{t("Size")}</span><strong>{formatBytes(card.config.bytes)}</strong></div>
      </div>

      {!deployed && <>{!imported && (
        <>
        <button type="button" aria-label={t('Import image')} disabled={uploading} className={`upload-zone ${uploading ? "is-uploading" : ""}`}
          onClick={() => fileInput.current?.click()}>
          <UploadCloud size={17} />
          <span>{uploading ? t("Importing managed copy…") : t("Import image")}</span>
        </button>
          <input
            ref={fileInput}
            hidden
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            disabled={uploading}
            onChange={(event) => {
              if (event.target.files) void intake.pick(event.target.files, undefined);
              event.currentTarget.value = "";
            }}
          />
        </>
      )}
      {error && <p role="alert" className="ui-error">{error}</p>}

      <section className="card-section">
        <div className="section-heading"><span>{t("Relationships")}</span><small>{t("read-only resource")}</small></div>
        <RelationshipList card={card} />
      </section></>}
      <FileDropOverlay visible={intake.hovering}>{t('Drop image here')}</FileDropOverlay>
    </div>
  );
}
