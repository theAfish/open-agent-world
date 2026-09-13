import type { WorldCard, WorldEdge, FlowViewportState } from '../types/world';
import type { NodeSurfaceLevel } from '../state/nodeSurfaces';
import type { GlueBond } from '../state/glue';
import type { WorldInteraction } from '../state/interactions';

export type Role = 'demo' | 'practice' | 'agent' | 'conversation' | 'sandbox' | 'glueA' | 'glueB' | 'minister';
export type Target = Role | 'center' | 'terrain' | 'deck' | 'tools';
export type Demonstration = 'deck' | 'place' | 'connect' | 'glue' | 'unglue' | 'minister';
export interface TutorialStep {
  id: string;
  chapter: number;
  dialogue: string;
  hint?: string;
  target: Target;
  action?: Demonstration;
  button?: string;
  expects?: 'pan' | 'zoom' | 'place' | 'move' | 'select' | 'open' | 'close' | 'focus' | 'delete' | 'configure' | 'workspace' | 'message' | 'connect' | 'glue' | 'presence';
  role?: Role;
  optional?: string;
  review?: boolean;
}

export const CHAPTERS = ['Find your bearings', 'Make it tangible', 'Build a working world', 'Stick together', 'Meet the Minister'];
export const STEPS: readonly TutorialStep[] = [
  { id: 'enter', chapter: 0, dialogue: 'Oh, hello! There’s a whole world beyond this little ring. Come explore with me.', target: 'center', button: 'Let’s go' },
  { id: 'pan', chapter: 0, dialogue: 'Follow me over here. Drag an empty patch of canvas to move around.', hint: 'Drag the background, away from cards.', target: 'terrain', expects: 'pan' },
  { id: 'zoom', chapter: 0, dialogue: 'A little closer… or a little further. Scroll to zoom. The + and − buttons work too.', target: 'tools', expects: 'zoom' },
  { id: 'deck', chapter: 1, dialogue: 'Cards come from packs, then live in your deck. Let’s collect the essentials and add them to your active deck.', target: 'deck', action: 'deck', button: 'Prepare my deck' },
  { id: 'place-demo', chapter: 1, dialogue: 'Watch this Text card travel from the deck into the world. This one is my temporary prop.', target: 'deck', action: 'place', button: 'Show me' },
  { id: 'place', chapter: 1, dialogue: 'Your turn! Drag a Text card from the deck onto a clear patch. Clicking the deck card also places it.', target: 'deck', expects: 'place', role: 'practice' },
  { id: 'move', chapter: 1, dialogue: 'Give your card a new home. Drag its title or an empty part of its frame.', target: 'practice', expects: 'move', role: 'practice' },
  { id: 'select', chapter: 1, dialogue: 'Shift-click selects a card. You can select several this way, too.', target: 'practice', expects: 'select', role: 'practice' },
  { id: 'open', chapter: 1, dialogue: 'Now click your card normally to look inside.', target: 'practice', expects: 'open', role: 'practice' },
  { id: 'close', chapter: 1, dialogue: 'The × closes this inspector. The corner arrow folds a preview down to a small node.', hint: 'Close the inspector with × or Escape.', target: 'practice', expects: 'close', role: 'practice' },
  { id: 'focus', chapter: 1, dialogue: 'Lost your place? Select your card, then press F to focus it. The fit-view button can show the whole world.', hint: 'Shift-click your card, then press F outside a text field.', target: 'practice', expects: 'focus', role: 'practice' },
  { id: 'delete', chapter: 1, dialogue: 'Let’s remove your practice card. Select it and press Delete. Ctrl / ⌘ Z can bring it back.', hint: 'The normal Remove button inside the inspector works too.', target: 'practice', expects: 'delete', role: 'practice' },
  { id: 'workflow', chapter: 2, dialogue: 'Now for something useful: an Agent to think, a Conversation to talk in, and a Sandbox to work in. These cards will be yours to keep.', target: 'center', button: 'Build my first workflow' },
  { id: 'agent', chapter: 2, dialogue: 'Place an Agent from your deck. Leave some room beside it for its Conversation.', target: 'deck', expects: 'place', role: 'agent' },
  { id: 'configure', chapter: 2, dialogue: 'Open your Agent. Give it a short system instruction, like “Help me plan a small garden.” Pick a model here, or use Manage models.', hint: 'Edits save when you leave the field. Continue when your settings are ready.', target: 'agent', expects: 'configure', role: 'agent', optional: 'Keep these settings', review: true },
  { id: 'conversation', chapter: 2, dialogue: 'Place a Conversation beside your Agent. It holds durable messages and sessions.', target: 'deck', expects: 'place', role: 'conversation' },
  { id: 'connect-demo', chapter: 2, dialogue: 'I’ll connect these with Participate. A connection grants a real capability: this Agent can now join the Conversation.', target: 'agent', action: 'connect', button: 'Show the connection' },
  { id: 'conversation-open', chapter: 2, dialogue: 'Click the Conversation, then its Open workspace button. That’s where your sessions and messages live.', target: 'conversation', expects: 'workspace', role: 'conversation' },
  { id: 'message', chapter: 2, dialogue: 'Try “Help me plan a small garden.” Choose the Agent in the session or @mention it, then send. A configured model is needed for its reply.', hint: 'Your message stays in the Conversation even when a model is unavailable.', target: 'conversation', expects: 'message', role: 'conversation', optional: 'Try the model later' },
  { id: 'reply', chapter: 2, dialogue: 'The Agent’s replies and tool activity appear in this session. Take a look around; your conversation stays here when you close the card.', target: 'conversation', button: 'On to Sandbox' },
  { id: 'sandbox', chapter: 2, dialogue: 'Now place a Sandbox. It gives an Agent a workspace and the ability to execute commands.', target: 'deck', expects: 'place', role: 'sandbox' },
  { id: 'sandbox-connect', chapter: 2, dialogue: 'Your turn to connect! Drag from an edge port of the Agent to the Sandbox. Choose Execute, or Execute + Start/Stop, then grant it.', hint: 'If a port is covered, move a card aside. Execute needs a running Sandbox; Start/Stop also lets the Agent manage it.', target: 'sandbox', expects: 'connect', role: 'sandbox' },
  { id: 'sandbox-open', chapter: 2, dialogue: 'Open the Sandbox to see its runtime and workspace settings. Start it here when you’re ready to execute. Connections compose what an Agent can do.', target: 'sandbox', expects: 'open', role: 'sandbox' },
  { id: 'sandbox-ready', chapter: 2, dialogue: 'This is where you choose the runtime and workspace, then start your Sandbox. We can leave it stopped for now. Your three cards already describe a working system.', target: 'sandbox', button: 'Try sticking cards' },
  { id: 'glue-demo', chapter: 3, dialogue: 'Cards can stick together physically, too. Watch these two temporary Text cards meet at their edges and move as one.', target: 'center', action: 'glue', button: 'Show me sticking' },
  { id: 'glue-reset', chapter: 3, dialogue: 'See the seam? Sticking arranges cards; it doesn’t grant a capability. I’ll separate my props so you can try.', target: 'glueA', action: 'unglue', button: 'My turn' },
  { id: 'glue', chapter: 3, dialogue: 'Switch on the droplet tool (Glue). Drag one of my two Text cards close to the other’s edge; release when the seam appears.', target: 'tools', expects: 'glue', role: 'glueA' },
  { id: 'glue-move', chapter: 3, dialogue: 'Now drag either card. Its neighbour comes along! Select the pair to find Detach glue when you want to separate them.', target: 'glueA', expects: 'move', role: 'glueA' },
  { id: 'minister', chapter: 4, dialogue: 'One last neighbour: the Minister. It can help operate and configure the world inside its circle.', target: 'center', action: 'minister', button: 'Place Minister Card' },
  { id: 'minister-presence', chapter: 4, dialogue: 'Move onto the Minister’s circle or click it. You can speak right here on the canvas.', target: 'minister', expects: 'presence', role: 'minister' },
  { id: 'minister-message', chapter: 4, dialogue: 'Try asking “What cards are inside your circle?” Later, ask it to find a card, arrange your world, or help configure an Agent.', target: 'minister', expects: 'message', role: 'minister', optional: 'Try the model later' },
  { id: 'minister-history', chapter: 4, dialogue: 'The little settings button beside its circle opens history, settings, nearby cards, and confirmations. Open it now.', target: 'minister', expects: 'open', role: 'minister' },
  { id: 'minister-safety', chapter: 4, dialogue: 'You stay in charge. Review sensitive or destructive proposals in the Minister’s confirmations. Its control radius and canvas-edit setting define where it can help.', target: 'minister', button: 'Got it' },
  { id: 'finish', chapter: 4, dialogue: 'You’ve made a world! Your workflow stays. I’ll tidy my temporary props. Find Replay Tutorial at the compass in the world controls whenever you want another walk.', target: 'center', button: 'Finish & keep my world' },
];

export interface Observation {
  cards: WorldCard[];
  edges: WorldEdge[];
  selected: string[];
  surfaces: Record<string, NodeSurfaceLevel>;
  bonds: GlueBond[];
  viewport: FlowViewportState;
  settled: boolean;
  deleted: string[];
}
export interface Baseline { position?: { x: number; y: number }; viewport: FlowViewportState; config?: string }

export function stepComplete(step: TutorialStep, refs: Partial<Record<Role, string>>, baseline: Baseline, state: Observation, event?: WorldInteraction) {
  const id = step.role && refs[step.role];
  const card = state.cards.find(item => item.id === id);
  const level = id ? state.surfaces[id] ?? 'preview' : 'node';
  switch (step.expects) {
    case 'pan': return event?.type === 'viewport' && Math.hypot(event.x - baseline.viewport.x, event.y - baseline.viewport.y) > 45;
    case 'zoom': return event?.type === 'viewport' && Math.abs(event.zoom - baseline.viewport.zoom) > .07;
    case 'place': return Boolean(card);
    case 'move': return Boolean(card && baseline.position && state.settled && Math.hypot(card.position.x - baseline.position.x, card.position.y - baseline.position.y) > 35);
    case 'select': return Boolean(id && state.selected.includes(id));
    case 'open': return Boolean(card && (level === 'inspector' || level === 'workspace'));
    case 'close': return Boolean(card && (level === 'preview' || level === 'node'));
    case 'focus': return Boolean(id && event?.type === 'focus' && event.ids.includes(id));
    case 'delete': return Boolean(id && state.deleted.includes(id));
    case 'configure': return Boolean(card && state.settled && (level === 'inspector' || level === 'workspace') && JSON.stringify(card.config) !== baseline.config);
    case 'workspace': return Boolean(card && level === 'workspace');
    case 'message': return Boolean(id && event?.type === 'message-sent' && event.cardId === id);
    case 'presence': return Boolean(id && event?.type === 'minister-opened' && event.cardId === id);
    case 'connect': return state.settled && state.edges.some(edge => edge.source === refs.agent && edge.target === refs.sandbox && ['execute', 'execute_manage'].includes(edge.relationship));
    case 'glue': return event?.type === 'glue-saved' && event.bonds.some(bond => [bond.a, bond.b].includes(refs.glueA ?? '') && [bond.a, bond.b].includes(refs.glueB ?? ''));
    default: return false;
  }
}
