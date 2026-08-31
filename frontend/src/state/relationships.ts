import type { CardType, Relationship, WorldEdge } from "../types/world";

export interface RelationshipOption {
  value: Relationship;
  label: string;
  shortLabel: string;
  description: string;
}

const RELATIONSHIP_OPTIONS: Record<Relationship, RelationshipOption> = {
  communicate: {
    value: "communicate",
    label: "Communicate",
    shortLabel: "message",
    description: "The agent can send a scoped message to this agent and receive its response.",
  },
  read: {
    value: "read",
    label: "Read",
    shortLabel: "read",
    description: "The agent can inspect this text through a scoped tool.",
  },
  read_edit: {
    value: "read_edit",
    label: "Read + edit",
    shortLabel: "read + edit",
    description: "The agent can inspect and modify this text through scoped tools.",
  },
  view: {
    value: "view",
    label: "View",
    shortLabel: "view",
    description: "The agent can inspect the image content.",
  },
  execute: {
    value: "execute",
    label: "Execute",
    shortLabel: "execute",
    description: "The agent can run commands in this isolated workplace.",
  },
  mount_read_only: {
    value: "mount_read_only",
    label: "Mount read-only",
    shortLabel: "read-only",
    description: "The resource is visible in the sandbox but cannot be changed there.",
  },
  mount_read_write: {
    value: "mount_read_write",
    label: "Mount read/write",
    shortLabel: "read/write",
    description: "The text resource can be read and changed inside the sandbox.",
  },
};

const VALID_RELATIONSHIPS: Partial<Record<CardType, Partial<Record<CardType, Relationship[]>>>> = {
  agent: {
    agent: ["communicate"],
    text: ["read", "read_edit"],
    image: ["view"],
    sandbox: ["execute"],
  },
  text: {
    sandbox: ["mount_read_only", "mount_read_write"],
  },
  image: {
    sandbox: ["mount_read_only"],
  },
};

export function getRelationshipOptions(
  sourceType: CardType,
  targetType: CardType,
): RelationshipOption[] {
  return (VALID_RELATIONSHIPS[sourceType]?.[targetType] ?? []).map(
    (relationship) => RELATIONSHIP_OPTIONS[relationship],
  );
}

export function getRelationshipOption(relationship: Relationship): RelationshipOption {
  return RELATIONSHIP_OPTIONS[relationship];
}

export interface ConnectionValidation {
  valid: boolean;
  reason?: string;
  options: RelationshipOption[];
}

export function validateConnection(
  sourceId: string | null | undefined,
  targetId: string | null | undefined,
  sourceType: CardType | undefined,
  targetType: CardType | undefined,
  edges: WorldEdge[] = [],
): ConnectionValidation {
  if (!sourceId || !targetId || !sourceType || !targetType) {
    return { valid: false, reason: "Choose two world objects.", options: [] };
  }
  if (sourceId === targetId) {
    return { valid: false, reason: "An object cannot connect to itself.", options: [] };
  }

  const options = getRelationshipOptions(sourceType, targetType);
  if (options.length === 0) {
    return {
      valid: false,
      reason: `A ${sourceType} cannot grant a capability to a ${targetType} in this direction.`,
      options: [],
    };
  }

  if (edges.some((edge) => edge.source === sourceId && edge.target === targetId)) {
    return {
      valid: false,
      reason: "These objects already have a relationship. Select its edge to change or revoke it.",
      options,
    };
  }

  return { valid: true, options };
}

export function isRelationshipValid(
  sourceType: CardType,
  targetType: CardType,
  relationship: Relationship,
): boolean {
  return getRelationshipOptions(sourceType, targetType).some(
    (option) => option.value === relationship,
  );
}
