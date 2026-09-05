import {
  type CardConfig,
  type CardStatus,
  type CardType,
  type NodeTypeCatalogItem,
  type WorldCard,
  type WorldPosition,
} from "../types/world";

const DEFAULT_CONFIG: Record<CardType, CardConfig> = {
  agent: {
    system_instruction: "You are a careful research agent. Use only capabilities connected in this world.",
    model: "gemini-3.7-flash",
    prompt: "",
    output: [],
  },
  conversation: {
    description: "A shared field for durable human and agent conversations.",
  },
  text: {
    filename: "field-notes.txt",
    content: "",
    preview: "Empty text resource",
    history: [],
    revision: 0,
  },
  image: {
    filename: "untitled-image.png",
    mime_type: "image/png",
  },
  sandbox: {
    output: [],
    active_command: "",
    runtime: "auto",
    workspace_path: null,
    workspace_access: "read_write",
  },
};

const DEFAULT_NAME: Record<CardType, string> = {
  agent: "Atlas",
  conversation: "Conversation",
  text: "Field notes",
  image: "Reference image",
  sandbox: "North lab",
};

const DEFAULT_STATUS: Record<CardType, CardStatus> = {
  agent: "idle",
  conversation: "available",
  text: "available",
  image: "available",
  sandbox: "stopped",
};

export function buildCardDraft(
  type: CardType,
  position: WorldPosition,
  definition?: NodeTypeCatalogItem,
): Omit<WorldCard, "id"> {
  return {
    type,
    name: definition?.default_name ?? DEFAULT_NAME[type] ?? `New ${type}`,
    position,
    size: { width: 96, height: 96 },
    expanded: false,
    status: definition?.default_status ?? DEFAULT_STATUS[type] ?? "available",
    config: {
      ...(definition?.default_config ?? {}),
      ...(DEFAULT_CONFIG[type] ?? {}),
    },
  };
}

export function mergeCardPatch(
  card: WorldCard,
  patch: Partial<Omit<WorldCard, "id" | "type">>,
): WorldCard {
  return {
    ...card,
    ...patch,
    position: patch.position ? { ...card.position, ...patch.position } : card.position,
    size: patch.size ? { ...card.size, ...patch.size } : card.size,
    config: patch.config ? { ...card.config, ...patch.config } : card.config,
  };
}

export function makeStressCards(count: number, seed = 7391): WorldCard[] {
  let state = seed >>> 0;
  const random = () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0xffffffff;
  };
  const types: CardType[] = ["agent", "text", "image", "sandbox"];

  return Array.from({ length: count }, (_, index) => {
    const type = types[index % types.length];
    const position = {
      x: Math.round((random() - 0.5) * 32000),
      y: Math.round((random() - 0.5) * 24000),
    };
    return {
      id: `stress-${index}`,
      ...buildCardDraft(type, position),
      name: `${type === "sandbox" ? "Lab" : type === "text" ? "Note" : type === "image" ? "Plate" : "Agent"} ${String(index + 1).padStart(3, "0")}`,
      ephemeral: true,
    };
  });
}
