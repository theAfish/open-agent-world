import { ImagePlus, UploadCloud } from "lucide-react";
import { useId, useState } from "react";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import { RelationshipList } from "./CardUtilities";

function formatBytes(bytes: unknown): string {
  if (typeof bytes !== "number" || !Number.isFinite(bytes)) return "No file imported";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ImageCardBody({ card }: { card: WorldCard }) {
  const inputId = useId();
  const uploadImage = useWorldStore((state) => state.uploadImage);
  const [uploading, setUploading] = useState(false);
  const previewUrl = typeof card.config.preview_url === "string" ? card.config.preview_url : undefined;
  const filename = String(card.config.filename ?? card.name);
  const imported = Boolean(previewUrl || Number(card.config.revision ?? 0) > 0);

  const importFile = async (file?: File) => {
    if (!file) return;
    setUploading(true);
    await uploadImage(card.id, file);
    setUploading(false);
  };

  const preview = previewUrl ? (
    <img src={previewUrl} alt={`Preview of ${filename}`} draggable={false} />
  ) : (
    <div className="image-placeholder" aria-label="No image imported">
      <span><ImagePlus size={26} /></span>
      <i /><i /><i />
      <small>Awaiting image</small>
    </div>
  );

  if (!card.expanded) {
    return (
      <>
        <div className="image-preview-compact">{preview}</div>
        <div className="compact-meta">
          <span title={filename}>{filename}</span>
          <span>{card.config.image_width && card.config.image_height
            ? `${card.config.image_width} × ${card.config.image_height}`
            : "unresolved"}</span>
        </div>
      </>
    );
  }

  return (
    <div className="expanded-stack nodrag nopan">
      <div className="image-preview-expanded">{preview}</div>
      <div className="image-metadata-grid">
        <div><span>Filename</span><strong title={filename}>{filename}</strong></div>
        <div><span>Dimensions</span><strong>{card.config.image_width && card.config.image_height
          ? `${card.config.image_width} × ${card.config.image_height}`
          : "Not available"}</strong></div>
        <div><span>Format</span><strong>{String(card.config.mime_type ?? "Unknown")}</strong></div>
        <div><span>Size</span><strong>{formatBytes(card.config.bytes)}</strong></div>
      </div>

      {imported ? (
        <div className="upload-zone upload-zone--locked">
          <UploadCloud size={17} />
          <span>Managed image imported</span>
          <small>Image resources are immutable in this POC. Create a new Image card to import another file.</small>
        </div>
      ) : (
        <label htmlFor={inputId} className={`upload-zone ${uploading ? "is-uploading" : ""}`}>
          <UploadCloud size={17} />
          <span>{uploading ? "Importing managed copy…" : "Import image"}</span>
          <small>PNG, JPEG, GIF or WebP</small>
          <input
            id={inputId}
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            disabled={uploading}
            onChange={(event) => {
              void importFile(event.target.files?.[0]);
              event.currentTarget.value = "";
            }}
          />
        </label>
      )}

      <section className="card-section">
        <div className="section-heading"><span>Relationships</span><small>read-only resource</small></div>
        <RelationshipList card={card} />
      </section>
    </div>
  );
}
