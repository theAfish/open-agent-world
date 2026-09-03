interface MentionableAgent {
  id: string;
  name: string;
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
      `(^|\\s)@${escapePattern(agent.name)}(?=$|[\\s,.:;!?])`,
      "iu",
    );
    return mention.test(content);
  }).map((agent) => agent.id);
  if (targets.length > 0) return [...new Set(targets)];
  if (fallbackAgentId && participants.some((agent) => agent.id === fallbackAgentId)) {
    return [fallbackAgentId];
  }
  return participants.length === 1 ? [participants[0].id] : [];
}

export function appendMention(content: string, agentName: string): string {
  const prefix = content && !/\s$/.test(content) ? `${content} ` : content;
  return `${prefix}@${agentName} `;
}
