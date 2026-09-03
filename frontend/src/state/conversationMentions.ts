interface MentionableAgent {
  id: string;
  name: string;
}

export interface MentionCompletion {
  start: number;
  end: number;
  query: string;
  candidates: MentionableAgent[];
}

function escapePattern(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function resolveConversationTargets(
  content: string,
  participants: MentionableAgent[],
  fallbackAgentId?: string,
): string[] {
  const targets = participants.filter((agent) => {
    const mention = new RegExp(
      `@${escapePattern(agent.name)}(?=$|[\\s,.:;!?，。；：！？])`,
      "iu",
    );
    return mention.test(content);
  }).map((agent) => agent.id);
  if (targets.length > 0) return [...new Set(targets)];
  if (content.includes("@")) return [];
  if (fallbackAgentId && participants.some((agent) => agent.id === fallbackAgentId)) {
    return [fallbackAgentId];
  }
  return participants.length === 1 ? [participants[0].id] : [];
}

export function mentionCompletion(
  content: string,
  caret: number,
  participants: MentionableAgent[],
): MentionCompletion | undefined {
  const end = Math.max(0, Math.min(caret, content.length));
  const beforeCaret = content.slice(0, end);
  const start = beforeCaret.lastIndexOf("@");
  if (start < 0) return undefined;
  const query = content.slice(start + 1, end);
  if (query.includes("@") || /[\r\n]/u.test(query) || query.length > 80) return undefined;
  const normalized = query.toLocaleLowerCase();
  const candidates = participants
    .filter((agent) => agent.name.toLocaleLowerCase().includes(normalized))
    .sort((left, right) => {
      const leftStarts = left.name.toLocaleLowerCase().startsWith(normalized);
      const rightStarts = right.name.toLocaleLowerCase().startsWith(normalized);
      if (leftStarts !== rightStarts) return leftStarts ? -1 : 1;
      return left.name.localeCompare(right.name);
    });
  if (candidates.length === 0) return undefined;
  return { start, end, query, candidates };
}

export function completeMention(
  content: string,
  completion: Pick<MentionCompletion, "start" | "end">,
  agentName: string,
): { content: string; caret: number } {
  const suffix = content.slice(completion.end);
  const inserted = `@${agentName}${/^\s/u.test(suffix) ? "" : " "}`;
  return {
    content: `${content.slice(0, completion.start)}${inserted}${suffix}`,
    caret: completion.start + inserted.length,
  };
}

export function appendMention(content: string, agentName: string): string {
  const prefix = content && !/\s$/.test(content) ? `${content} ` : content;
  return `${prefix}@${agentName} `;
}
