import { t } from "../i18n";
import { availableModels, type ModelCatalog } from "../state/modelConnections";

export function modelLabel(catalog: ModelCatalog, selected: unknown): string {
  const reference = !selected || selected === "oaw:default" ? catalog.default_model : String(selected);
  if (!reference) return t("Choose a default model");
  return availableModels(catalog).find(model => model.value === reference)?.label
    ?? (reference.startsWith("oaw:model:") ? t("Unavailable model") : reference);
}
