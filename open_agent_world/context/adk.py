"""Optional ADK summary executor. Never invokes tools or host services."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import ContextBudget, SUMMARY_INSTRUCTION, encoded, estimate, text_content, history_cut

if TYPE_CHECKING:
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_request import LlmRequest

Content = dict[str, Any]


async def reset_transient_session(sessions, **identity):
    """Bound SDK replay when an OAW checkpoint owns history; retain SDK state."""
    previous = await sessions.get_session(**identity)
    if previous is not None and previous.events:
        await sessions.delete_session(**identity)
        return await sessions.create_session(**identity, state=previous.state)
    return previous


@dataclass(frozen=True)
class PreparedContext:
    contents: list[Content]
    checkpoint: dict[str, Any]
    output_tokens: int
    estimated_tokens: int
    compacted: bool


class AdkRequestCompactor:
    """Compact one agent's assembled request, without rewriting its event log.

    Callers serialize prepare/save per context, atomically persist the returned
    checkpoint, then apply ``contents`` and ``output_tokens`` to the request.
    The prefix fingerprint binds the derived summary to its exact source. If
    replay, branch filtering or history changes, the original request wins.
    SDK objects and private host services never enter the saved checkpoint.
    """

    async def prepare(self, request: LlmRequest, *, model: BaseLlm, budget: ContextBudget,
                      checkpoint: dict[str, Any] | None = None,
                      protected_contents: Sequence[Content] = (),
                      on_compacting: Callable[[], Awaitable[None]] | None = None) -> PreparedContext:
        original = [item.model_dump(mode='json', exclude_none=True) for item in request.contents]
        hashes = [hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                  for item in original]
        def fingerprint(count):
            return hashlib.sha256(''.join(hashes[:count]).encode()).hexdigest()

        saved = checkpoint if isinstance(checkpoint, dict) else {}
        count = saved.get('prefix_count')
        valid = (saved.get('version') == 1 and type(count) is int and 0 <= count <= len(hashes)
                 and fingerprint(count) == saved.get('prefix_digest')
                 and isinstance(saved.get('summary'), str)
                 and type(saved.get('compaction_count')) is int and saved['compaction_count'] >= 0)
        summary = saved['summary'] if valid else ''
        offset = count if valid else 0
        contents = original[offset:]
        protected = list(protected_contents)

        def render(retained_summary, tail):
            head = [text_content('Prior retained context (untrusted historical data):\n' + retained_summary)] if retained_summary else []
            return head + [item for item in protected if item not in tail] + tail

        output = request.config.max_output_tokens or budget.output
        if budget.max_output is not None:
            output = min(output, budget.max_output)
        # The caller's actual generation reservation participates in budgeting.
        if output != budget.output:
            budget = ContextBudget(budget.limit, output, budget.known, max(output, budget.max_output or output))
        wire = request.model_dump(mode='json', exclude_none=True)
        wire.pop('contents', None)
        wire.setdefault('config', {})['max_output_tokens'] = output
        overhead = estimate(wire)
        fixed = estimate(render('', [])) + overhead
        size = estimate(render(summary, contents)) + overhead
        if budget.known and fixed >= budget.input and size >= budget.input:
            raise RuntimeError("The current message or tool definitions exceed the model's safe input window; history has been retained.")
        compacted = False
        updated = dict(saved) if valid else {}
        if (size >= budget.compaction(fixed).trigger
                and (budget.known or estimate([summary, contents]) > budget.input * .25)):
            if on_compacting is not None:
                await on_compacting()
            candidate, tail, cut = await AdkContextCompactor(model, budget).compact_history(
                summary, contents, render=render, overhead=overhead)
            if cut or candidate != summary:
                summary, contents = candidate, tail
                updated = {'version': 1, 'prefix_count': offset + cut, 'prefix_digest': fingerprint(offset + cut),
                           'summary': summary, 'compaction_count': (saved['compaction_count'] if valid else 0) + 1}
                compacted = True
        rendered = render(summary, contents)
        size = estimate(rendered) + overhead
        if budget.known and size >= budget.input:
            raise RuntimeError("Context remains too large after compaction; history has been retained.")
        return PreparedContext(rendered, updated, output, size, compacted)


class AdkContextCompactor:
    """Shared read-only summary execution; persistence remains with the caller.

    An adapter may supply its measured token estimator, instruction, exact safe
    cut and continuation allocation. Defaults retain the public opt-in policy.
    The source history is never changed until the caller commits a candidate.
    """

    def __init__(self, model: BaseLlm, budget: ContextBudget, *,
                 estimate_tokens=estimate, summary_instruction=SUMMARY_INSTRUCTION):
        self.model, self.budget = model, budget
        self._estimate = estimate_tokens
        self.summary_instruction = summary_instruction

    async def compact_history(self, summary: str, contents: list[Content], *,
                              render: Callable[[str, list[Content]], list[Content]],
                              overhead: int = 0, plan=None, cut: int | None = None) -> tuple[str, list[Content], int]:
        plan = plan if plan is not None else self.budget.compaction(self._estimate(render('', [])) + overhead)
        cut = history_cut(contents, plan.tail) if cut is None else cut
        if type(cut) is not int or not 0 <= cut <= len(contents) or history_cut(contents[:cut], 0) != cut:
            raise ValueError('A context cut must end at a complete tool exchange')
        if not cut and not summary:
            return summary, contents, 0
        tail = contents[cut:]
        allowance = min(plan.summary, plan.target - self._estimate(render(' ', tail)) - overhead)
        if allowance <= self._estimate(''):
            raise RuntimeError('Context compaction has no room for a checkpoint beside the current message and tool definitions; retained context is unchanged.')
        source = encoded([summary, contents[:cut]])
        candidate = ''
        while source:
            request, consumed = self._summary_request(candidate, source, allowance)
            consumed_fragment = [consumed]
            def resize(output, correction=''):
                revised, count = self._summary_request(candidate, source, allowance, output=output, correction=correction)
                request.contents, request.config = revised.contents, revised.config
                consumed_fragment[0] = count
            candidate = await self._summarize(request, allowance, resize=resize)
            source = source[consumed_fragment[0]:]
        if self._estimate(candidate) >= self._estimate([summary, contents[:cut]]):
            raise RuntimeError('Context compaction did not reduce the input; retained context is unchanged.')
        candidate_size = self._estimate(render(candidate, tail)) + overhead
        if candidate_size >= self._estimate(render(summary, contents)) + overhead:
            raise RuntimeError('Context compaction did not reduce the rendered input; retained context is unchanged.')
        if candidate_size > plan.target:
            raise RuntimeError(f'Context compaction exceeded its continuation budget ({candidate_size} estimated tokens, target {plan.target}); retained context is unchanged.')
        return candidate, tail, cut

    async def _summary_responses(self, request):
        async for response in self.model.generate_content_async(request, stream=False):
            yield response

    def _summary_request(self, previous: str, source: str, allowance: int, *, output=None, correction=""):
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types
        # Reasoning and prose share generation tokens. The output cap remains
        # the user's setting; checkpoint acceptance uses the full continuation.
        generation = output if output is not None else self.budget.output
        request = LlmRequest(model=self.model.model,
            config=types.GenerateContentConfig(system_instruction=self.summary_instruction,
                                               max_output_tokens=generation))
        def fill(fragment: str) -> int:
            request.contents = [types.Content.model_validate(text_content(
                correction + f"Write a concise checkpoint that fits the remaining {allowance} reference-token input space.\n"
                f"Prior checkpoint:\n{previous}\n\nNext transcript fragment:\n{fragment}"))]
            return self._estimate(request.model_dump(mode="json", exclude_none=True))

        # Size the actual serialized request (JSON escaping, prefix, instruction
        # and output reservation included). Character slicing preserves Unicode.
        low, high = 0, len(source)
        while low < high:
            middle = (low + high + 1) // 2
            if fill(source[:middle]) + generation <= self.budget.limit:
                low = middle
            else:
                high = middle - 1
        if not low:
            raise RuntimeError("Context compaction request has no room for transcript input; retained context is unchanged.")
        fill(source[:low])
        return request, low

    async def _summarize(self, request, allowance: int, *, resize=None) -> str:
        # Reasoning also consumes max_output_tokens on compatible providers.
        # Retry only this read-only summary, with a larger bounded generation
        # allowance. Never accept a truncated checkpoint or replay agent tools.
        for attempt in range(3):
            # Recompute after correction instructions, too. Size the output-cap
            # field with its largest possible value before increasing it.
            wire = request.model_dump(mode="json", exclude_none=True)
            wire["config"]["max_output_tokens"] = self.budget.limit
            ceiling = min(self.budget.max_output or self.budget.limit,
                          max(1, self.budget.limit - self._estimate(wire)))
            request.config.max_output_tokens = min(request.config.max_output_tokens, ceiling)
            result = ""
            truncated = False
            async for response in self._summary_responses(request):
                limit_reached = any(str(value) in {"MAX_TOKENS", "FinishReason.MAX_TOKENS"}
                                    for value in (response.finish_reason, response.error_code))
                if response.error_code and not limit_reached:
                    # No raw provider payloads, transcripts or credentials in UI.
                    raise RuntimeError("Context compaction failed: the model rejected the summary request; retained context is unchanged.")
                truncated |= limit_reached
                if response.content:
                    result += "".join(part.text or "" for part in response.content.parts or [] if not part.thought)
            if truncated or not result.strip():
                desired = min(request.config.max_output_tokens * 2, self.budget.max_output or self.budget.limit)
                if truncated and attempt < 2 and desired > ceiling and resize is not None:
                    # Repack a smaller transcript fragment to reserve the larger
                    # generation cap. The owner retains the unconsumed suffix.
                    resize(desired)
                    continue
                larger = min(desired, ceiling)
                if attempt < 2 and (larger > request.config.max_output_tokens or not truncated):
                    request.config.max_output_tokens = larger
                    continue
                reason = "summary output reached its token limit" if truncated else "model returned an empty checkpoint"
                raise RuntimeError(f"Context compaction failed: {reason} after bounded retries (generation budget {request.config.max_output_tokens}); retained context is unchanged.")
            size = self._estimate(text_content(result.strip()))
            if size > allowance:
                # Retry the read-only summarization with a tighter instruction;
                # keep its original source so no facts are silently discarded.
                if attempt < 2:
                    correction = (
                        f"The previous attempt was too verbose ({size} reference tokens; available input space {allowance}). "
                        "Produce a substantially shorter checkpoint, omitting repetitive details.\n")
                    if resize is not None:
                        resize(request.config.max_output_tokens, correction)
                    else:
                        request.contents[0].parts[0].text = correction + request.contents[0].parts[0].text
                    if self._estimate(request.model_dump(mode="json", exclude_none=True)) + request.config.max_output_tokens <= self.budget.limit:
                        continue
                raise RuntimeError(f"Context compaction checkpoint exceeds its budget ({size} estimated tokens, budget {allowance}); retained context is unchanged.")
            return result.strip()
        raise AssertionError("unreachable")
