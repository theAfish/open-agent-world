import type { WorldCard, WorldEdge, FlowViewportState } from '../types/world';
import type { NodeSurfaceLevel } from '../state/nodeSurfaces';
import type { GlueBond } from '../state/glue';
import type { LibrarySnapshot } from '../state/cardLibrary';
import type { WorldInteraction } from '../state/interactions';
import { paneViews, readWorkspaceLayout } from '../legions/workspaceLayout';

export type Role = 'demo' | 'practice' | 'agent' | 'conversation' | 'sandbox' | 'glueA' | 'glueB' | 'ministerRole' | 'minister' | 'legion';
export type Target = Role | 'center' | 'terrain' | 'deck' | 'tools' | 'zoom-controls' | 'library' | 'library-pack' | 'library-decks' | 'settings' | 'model-connection' | 'model-credentials' | 'model-list' | 'model-save' | 'legion-selection' | 'legion-layout' | 'legion-back';
export type Demonstration = 'library' | 'deck' | 'place' | 'connect' | 'glue' | 'unglue';
export interface TutorialStep {
  id: string;
  chapter: number;
  dialogue: string;
  hint?: string;
  target: Target;
  action?: Demonstration;
  participants?: Role[];
  result?: { dialogue: string; target: Target };
  button?: string;
  expects?: 'library-open' | 'pack-open' | 'library-cards' | 'deck-ready' | 'settings-open' | 'model-connection' | 'models-saved' | 'pan' | 'zoom' | 'place' | 'move' | 'select' | 'open' | 'close' | 'focus' | 'delete' | 'configure' | 'workspace' | 'message' | 'connect' | 'glue' | 'presence' | 'promotion' | 'minister-panel' | 'legion' | 'legion-workspace' | 'legion-layout' | 'legion-canvas';
  role?: Role;
  optional?: string;
  review?: boolean;
}

export const CHAPTERS = ['Find your bearings', 'Make it tangible', 'Build a working world', 'Stick together', 'Meet the Minister', 'Build a Legion workspace'];
export const STEPS: readonly TutorialStep[] = [
  { id: 'enter', chapter: 0, dialogue: 'Oh, hello! There’s a whole world beyond this little ring. Come explore with me.', target: 'center', button: 'Let’s go' },
  { id: 'pan', chapter: 0, dialogue: 'Follow me over here. Drag an empty patch of canvas to move around.', hint: 'Drag the background, away from cards.', target: 'terrain', expects: 'pan' },
  { id: 'zoom', chapter: 0, dialogue: 'A little closer… or a little further. Scroll to zoom. The + and − buttons work too.', target: 'zoom-controls', expects: 'zoom' },
  { id: 'deck', chapter: 1, dialogue: 'Let’s prepare your hand together. Open the Library here to find your first pack.', target: 'library', expects: 'library-open', action: 'library', button: 'Open Library' },
  { id: 'starter-pack', chapter: 1, dialogue: 'This pack contains the essentials. Click it to tear it open and collect its cards.', target: 'library-pack', expects: 'pack-open' },
  { id: 'starter-cards', chapter: 1, dialogue: 'Your cards are collected! Click the opened wrapper to browse them.', target: 'library-pack', expects: 'library-cards' },
  { id: 'deck-build', chapter: 1, dialogue: 'Drag Text, Agent, Conversation and Sandbox into your deck below. You can create a new deck there, too.', hint: 'You can also click Add to current deck. Switch decks using the tabs below.', target: 'library-decks', expects: 'deck-ready', review: true, button: 'Use this deck' },
  { id: 'place-demo', result: { target: 'demo', dialogue: 'The Text card is here. Take a moment to look; when you are ready, we will place yours.' }, chapter: 1, dialogue: 'Watch this Text card travel from the deck into the world. This one is my temporary prop.', target: 'deck', action: 'place', button: 'Show me' },
  { id: 'place', chapter: 1, dialogue: 'Your turn! Drag a Text card from the deck onto a clear patch. Clicking the deck card also places it.', target: 'deck', expects: 'place', role: 'practice' },
  { id: 'move', chapter: 1, dialogue: 'Give your card a new home. Drag its title or an empty part of its frame.', target: 'practice', expects: 'move', role: 'practice' },
  { id: 'select', chapter: 1, dialogue: 'Shift-click selects a card. Later, use Ctrl / ⌘-click to select several cards together.', target: 'practice', expects: 'select', role: 'practice' },
  { id: 'open', chapter: 1, dialogue: 'Now click your card normally to look inside.', target: 'practice', expects: 'open', role: 'practice' },
  { id: 'close', chapter: 1, dialogue: 'The × closes this inspector. The corner arrow folds a preview down to a small node.', hint: 'Close the inspector with × or Escape.', target: 'practice', expects: 'close', role: 'practice' },
  { id: 'focus', chapter: 1, dialogue: 'Lost your place? Select your card, then press F to focus it. The fit-view button can show the whole world.', hint: 'Shift-click your card, then press F outside a text field.', target: 'practice', expects: 'focus', role: 'practice' },
  { id: 'delete', chapter: 1, dialogue: 'Let’s remove your practice card. Select it and press Delete. Ctrl / ⌘ Z can bring it back.', hint: 'The normal Remove button inside the inspector works too.', target: 'practice', expects: 'delete', role: 'practice' },
  { id: 'workflow', chapter: 2, dialogue: 'Now for something useful: an Agent to think, a Conversation to talk in, and a Sandbox to work in. These cards will be yours to keep.', target: 'center', button: 'Build my first workflow' },
  { id: 'agent', chapter: 2, dialogue: 'Place an Agent. Then follow me to set up its model.', target: 'deck', expects: 'place', role: 'agent' },
  { id: 'model-settings', chapter: 2, dialogue: 'First, click this settings button to connect a model.', target: 'settings', expects: 'settings-open' },
  { id: 'model-connection', chapter: 2, dialogue: 'Choose a provider and click Add connection, or select an existing connection.', target: 'model-connection', expects: 'model-connection', optional: 'Use this connection' },
  { id: 'model-credentials', chapter: 2, dialogue: 'Enter your API key here and check the Base URL. Local services may not need a key.', target: 'model-credentials', button: 'Next' },
  { id: 'model-list', chapter: 2, dialogue: 'Click Add model and enter the model ID from your provider.', target: 'model-list', button: 'Next' },
  { id: 'model-save', chapter: 2, dialogue: 'Click Save settings to keep this connection.', target: 'model-save', expects: 'models-saved', optional: 'Set up later' },
  { id: 'configure', chapter: 2, dialogue: 'Back to your Agent: choose the model you saved and write a short system instruction.', target: 'agent', expects: 'configure', role: 'agent', optional: 'Keep these settings', review: true },
  { id: 'conversation', chapter: 2, dialogue: 'Place a Conversation beside your Agent. It holds durable messages and sessions.', target: 'deck', expects: 'place', role: 'conversation' },
  { id: 'connect-demo', result: { target: 'conversation', dialogue: 'Both cards are now connected with Participate. Follow the line from the Agent to the Conversation, then continue when you are ready.' }, participants: ['agent', 'conversation'], chapter: 2, dialogue: 'I’ll connect these with Participate. A connection grants a real capability: this Agent can now join the Conversation.', target: 'agent', action: 'connect', button: 'Show the connection' },
  { id: 'conversation-open', chapter: 2, dialogue: 'Conversation opens as a window for your sessions and messages. If you fold it into a card, click the card to reopen the window.', target: 'conversation', expects: 'workspace', role: 'conversation', review: true, button: 'Continue' },
  { id: 'message', chapter: 2, dialogue: 'Try “Help me plan a small garden.” Choose the Agent in the session or @mention it, then send. A configured model is needed for its reply.', hint: 'If the reply fails, check the API key, Base URL and model ID in Settings > Models. You can set them up later; your message stays here.', target: 'conversation', expects: 'message', role: 'conversation', optional: 'Try the model later' },
  { id: 'reply', chapter: 2, dialogue: 'The Agent’s replies and tool activity appear in this session. Take a look around; your conversation stays here when you close the card.', target: 'conversation', button: 'On to Sandbox' },
  { id: 'sandbox', chapter: 2, dialogue: 'Now place a Sandbox. It gives an Agent a workspace and the ability to execute commands.', target: 'deck', expects: 'place', role: 'sandbox' },
  { id: 'sandbox-connect', participants: ['agent', 'sandbox'], chapter: 2, dialogue: 'Your turn to connect! Drag from an edge port of the Agent to the Sandbox. Choose Execute, or Execute + Start/Stop, then grant it.', hint: 'If a port is covered, move a card aside. Execute needs a running Sandbox; Start/Stop also lets the Agent manage it.', target: 'sandbox', expects: 'connect', role: 'sandbox' },
  { id: 'sandbox-open', chapter: 2, dialogue: 'Sandbox opens as a window. Use the Settings tab to choose its runtime and working folder. Start it when you are ready to execute.', target: 'sandbox', expects: 'workspace', role: 'sandbox', review: true, button: 'Continue' },
  { id: 'sandbox-ready', chapter: 2, dialogue: 'This is where you choose the runtime and workspace, then start your Sandbox. We can leave it stopped for now. Your three cards already describe a working system.', target: 'sandbox', button: 'Try sticking cards' },
  { id: 'glue-demo', result: { target: 'glueA', dialogue: 'The two cards now share a seam and move together. Take a look before we separate them for your turn.' }, participants: ['glueA', 'glueB'], chapter: 3, dialogue: 'Cards can stick together physically, too. Watch these two temporary Text cards meet at their edges and move as one.', target: 'center', action: 'glue', button: 'Show me sticking' },
  { id: 'glue-reset', result: { target: 'glueA', dialogue: 'The cards are separate again. When you are ready, we will switch on Glue and bring their edges together.' }, participants: ['glueA', 'glueB'], chapter: 3, dialogue: 'See the seam? Sticking arranges cards; it doesn’t grant a capability. I’ll separate my props so you can try.', target: 'glueA', action: 'unglue', button: 'My turn' },
  { id: 'glue', participants: ['glueA', 'glueB'], chapter: 3, dialogue: 'Switch on the droplet tool (Glue). Drag one of my two Text cards close to the other’s edge; release when the seam appears.', target: 'tools', expects: 'glue', role: 'glueA' },
  { id: 'glue-move', participants: ['glueA', 'glueB'], chapter: 3, dialogue: 'Now drag either card. Its neighbour comes along! Select the pair to find Detach glue when you want to separate them.', target: 'glueA', expects: 'move', role: 'glueA' },
  { id: 'minister-card', chapter: 4, dialogue: 'Here is a new card in your deck: Minister role. Place it beside your Agent.', target: 'deck', expects: 'place', role: 'ministerRole' },
  { id: 'minister', chapter: 4, dialogue: 'Drag the Minister role card onto your Agent. The card is absorbed, and your Agent is promoted with its original model and tools.', participants: ['ministerRole', 'agent'], target: 'agent', expects: 'promotion', role: 'agent', review: true, button: 'Continue' },
  { id: 'minister-presence', chapter: 4, dialogue: 'Your Agent is now a Minister and still uses its original model and tools. Click its new Minister badge to talk right here on the canvas.', target: 'minister', expects: 'presence', role: 'minister' },
  { id: 'minister-message', chapter: 4, dialogue: 'Try asking “What cards are inside your circle?” Later, ask it to find a card, arrange your world, or help configure an Agent.', target: 'minister', expects: 'message', role: 'minister', optional: 'Try the model later' },
  { id: 'minister-history', chapter: 4, dialogue: 'Open your Agent card and choose the Minister tab. Its permissions and confirmations live there; history stays in the Agent workspace.', target: 'minister', expects: 'minister-panel', role: 'minister' },
  { id: 'minister-safety', chapter: 4, dialogue: 'You stay in charge. Review sensitive or destructive proposals in the Minister’s confirmations. Its control radius and canvas-edit setting define where it can help.', target: 'minister', button: 'Got it' },
  { id: 'legion-intro', chapter: 5, dialogue: 'Let’s give your workflow a home. A Legion groups cards while keeping their connections. Team mode is optional: enable it later for shared instructions and team state.', target: 'center', button: 'Build my Legion' },
  { id: 'legion-form', chapter: 5, dialogue: 'Hold Ctrl (⌘ on Mac) and click your Agent, Conversation and Sandbox, then click Form Legion in the selection bar.', hint: 'Select all three cards. Your existing cards and connections stay with you.', target: 'legion-selection', participants: ['agent', 'conversation', 'sandbox'], expects: 'legion' },
  { id: 'legion-open', chapter: 5, dialogue: 'Here is your Legion! Click Workspace mode in its header to bring its cards into one window.', target: 'legion', role: 'legion', expects: 'legion-workspace' },
  { id: 'legion-layout', chapter: 5, dialogue: 'Place Conversation first, then dock Sandbox beside it. Choose Done editing to save your layout.', hint: 'Drag from Workspace cards, or select a card and use Place selected card and Dock right. You can leave Agent unplaced.', target: 'legion-layout', role: 'legion', expects: 'legion-layout' },
  { id: 'legion-return', chapter: 5, dialogue: 'These are your live cards. Edit layout can split panels, add tabs or arrange individual sections. Your saved layout reopens with this Legion. Click Back to canvas when ready.', target: 'legion-back', role: 'legion', expects: 'legion-canvas' },
  { id: 'finish', chapter: 5, dialogue: 'Your Legion and workspace stay here. Later, save the Legion to your library to reuse its setup. I’ll tidy my temporary props. Replay Tutorial is at the compass whenever you want another walk.', target: 'center', button: 'Finish & keep my world' },
];

export interface Observation {
  cards: WorldCard[];
  edges: WorldEdge[];
  selected: string[];
  surfaces: Record<string, NodeSurfaceLevel>;
  bonds: GlueBond[];
  viewport: FlowViewportState;
  settled: boolean;
  settingsOpen?: boolean;
  legionWorkspaceId?: string;
  library?: { open: boolean; tab: string; snapshot: LibrarySnapshot | null };
  deleted: string[];
}
export interface Baseline { position?: { x: number; y: number }; viewport: FlowViewportState; config?: string }

export function stepComplete(step: TutorialStep, refs: Partial<Record<Role, string>>, baseline: Baseline, state: Observation, event?: WorldInteraction) {
  const id = step.role && refs[step.role];
  const card = state.cards.find(item => item.id === id);
  const level = id ? state.surfaces[id] ?? 'preview' : 'node';
  switch (step.expects) {
    case 'library-open': return Boolean(state.library?.open);
    case 'pack-open': return Boolean(state.library?.open && starterPack(state.library.snapshot)?.opened);
    case 'library-cards': return Boolean(state.library?.open && state.library.tab === 'cards');
    case 'deck-ready': {
      const library = state.library, snapshot = library?.snapshot;
      const deck = snapshot?.decks.find(item => item.id === snapshot.active_deck_id);
      return Boolean(library?.open && library.tab === 'cards' && deck && STARTER_CARDS.every(id => snapshot?.available_card_ids.includes(id) && deck.entries.some(entry => entry.kind === 'node' && entry.id === id)));
    }
    case 'settings-open': return Boolean(state.settingsOpen);
    case 'model-connection': return event?.type === 'model-connection-selected';
    case 'models-saved': return event?.type === 'models-saved';
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
    case 'legion': return Boolean(state.settled && refs.legion && state.cards.some(item => item.id === refs.legion && item.type === 'legion')
      && ['agent', 'conversation', 'sandbox'].every(role => state.cards.some(item => item.id === refs[role as Role] && item.parent_id === refs.legion)));
    case 'legion-workspace': return Boolean(card && state.legionWorkspaceId === id);
    case 'legion-layout': {
      const views = paneViews(readWorkspaceLayout(card?.config.workspace_layout).root);
      return Boolean(card && state.settled && state.legionWorkspaceId === id
        && ['conversation', 'sandbox'].every(role => views.some(view => view.card_id === refs[role as Role] && !view.section_id)));
    }
    case 'legion-canvas': return Boolean(card && !state.legionWorkspaceId && event?.type === 'legion-workspace-closed' && event.cardId === id);
    case 'message': return Boolean(id && event?.type === 'message-sent' && event.cardId === id);
    case 'promotion': return Boolean(card?.minister && refs.minister === card.id && state.settled);
    case 'minister-panel': return Boolean(id && event?.type === 'minister-settings-opened' && event.cardId === id);
    case 'presence': return Boolean(id && event?.type === 'minister-opened' && event.cardId === id);
    case 'connect': return state.settled && state.edges.some(edge => edge.source === refs.agent && edge.target === refs.sandbox && ['execute', 'execute_manage'].includes(edge.relationship));
    case 'glue': return event?.type === 'glue-saved' && event.bonds.some(bond => [bond.a, bond.b].includes(refs.glueA ?? '') && [bond.a, bond.b].includes(refs.glueB ?? ''));
    default: return false;
  }
}

export const STARTER_CARDS = ['text', 'agent', 'conversation', 'sandbox'];
export function starterPack(snapshot: LibrarySnapshot | null | undefined) {
  return Object.values(snapshot?.packs ?? {}).find(pack => pack.owned && STARTER_CARDS.every(id => pack.definition.cards.includes(id)));
}
