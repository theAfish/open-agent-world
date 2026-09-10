import { Activity, Bot, Boxes, Folder, Layers3, Sparkles, Star, Workflow, Zap } from "lucide-react";

export const DECK_ICONS = { bot: Bot, boxes: Boxes, workflow: Workflow, folder: Folder, layers: Layers3, sparkles: Sparkles, star: Star, zap: Zap, activity: Activity };

export function DeckIcon({ icon, size = 15 }: { icon?: string; size?: number }) {
  const Icon = DECK_ICONS[icon as keyof typeof DECK_ICONS] ?? Folder;
  return <Icon size={size} />;
}
