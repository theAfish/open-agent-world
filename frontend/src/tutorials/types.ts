/** Plain data carried by the catalog; no executable code or DOM selectors. */
export type TutorialText = string | { en: string; [locale: string]: string };
export interface TutorialStep { id: string; title: TutorialText; body: TutorialText }
export interface TutorialDefinition {
  id: string;
  revision?: number;
  title: TutorialText;
  summary: TutorialText;
  trigger?: 'encounter' | 'manual';
  steps?: TutorialStep[];
  document?: TutorialText | null;
  /** Tutorial IDs belonging to the same card type or Pack. */
  after?: string[];
}
export interface TutorialEntry {
  key: string;
  owner: string;
  ownerName: string;
  definition: TutorialDefinition;
}
export interface TutorialProgress {
  revision: number;
  status: 'reading' | 'dismissed' | 'completed';
  stepId?: string;
}
