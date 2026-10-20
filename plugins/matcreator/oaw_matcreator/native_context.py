"""MatCreator persistence adapter for OAW's opt-in context policy.

Each worker root already belongs to one OAW Agent/conversation. Within it,
session, branch and agent identity separate planner/executor/recursive agents.
No canonical ADK event or shared session state is rewritten by compaction.
"""
from __future__ import annotations

import asyncio
import hashlib
import json


class NativeContexts:
    def __init__(self, root, connection, emit):
        self.root = root / 'contexts'
        self.root.mkdir(parents=True, exist_ok=True)
        self.connection, self.emit = connection, emit
        self.locks = {}

    async def before_model(self, callback_context, request, protected_contents=()):
        from google.genai import types
        from open_agent_world.context import ContextBudget
        from open_agent_world.context.adk import AdkRequestCompactor

        invocation = callback_context._invocation_context
        session, agent = invocation.session, invocation.agent
        # Child runners allocate their own session IDs. Names alone would merge
        # parallel and recursive instances of the same step_executor Agent.
        identity = [session.app_name, session.user_id, session.id, invocation.branch, agent.name]
        key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            path = self.root / (key + '.json')
            try:
                saved = json.loads(path.read_text(encoding='utf-8'))
            except (FileNotFoundError, json.JSONDecodeError):
                # A derived checkpoint is replaceable. Canonical events still
                # build the full request; corrupt summaries never replace them.
                saved = {}
            model = agent.canonical_model
            configured = self.connection.get('model_id', self.connection.get('model'))
            adapter = self.connection['adapter']
            if adapter != 'legacy' and not configured.startswith(adapter + '/'):
                configured = adapter + '/' + configured
            window, output = self.connection.get('context_window'), self.connection.get('max_output_tokens')
            budget = (ContextBudget(window, output, max_output=output)
                      if model.model == configured and window and output else ContextBudget.for_model(model.model))
            protected = list(protected_contents)
            if invocation.user_content is not None:
                protected.insert(0, invocation.user_content.model_dump(mode='json', exclude_none=True))

            async def compacting():
                self.emit({'type': 'event', 'event': 'agent_progress', 'payload': {
                    'text': f'Compacting context for {agent.name}', 'context_id': key,
                    'author': agent.name, 'context_state': 'compacting'}})

            result = await AdkRequestCompactor().prepare(request, model=model, budget=budget,
                checkpoint=saved, protected_contents=protected, on_compacting=compacting)
            if result.checkpoint != saved:
                temp = path.with_suffix('.tmp')
                temp.write_text(json.dumps(result.checkpoint, ensure_ascii=False), encoding='utf-8')
                temp.replace(path)
            request.contents = [types.Content.model_validate(item) for item in result.contents]
            request.config.max_output_tokens = result.output_tokens
            if result.compacted:
                self.emit({'type': 'event', 'event': 'agent_progress', 'payload': {
                    'text': f'Context ready for {agent.name}', 'context_id': key,
                    'author': agent.name, 'context_state': 'normal',
                    'estimated_tokens': result.estimated_tokens}})
