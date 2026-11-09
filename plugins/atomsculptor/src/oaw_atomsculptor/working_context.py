"""Read-progress feedback from native request views; no separate history budget.

The native RequestWindow owns projection and compaction. This observer keeps
only page identities/ranges and never removes history, runs tools or decides
scientific acceptance.
"""
from __future__ import annotations

import json
from copy import deepcopy


def _merged(ranges):
    merged = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _key(page):
    return page['tool'], json.dumps(page['source'], sort_keys=True)


class RetrievalProgress:
    def __init__(self):
        self.state = None
        self.seen = set()
        self.feedback = None
        self.feedback_calls = set()

    def snapshot(self):
        return deepcopy(self.feedback)

    def observe(self, evidence, request, window):
        pages = window.read_pages(request)
        state = (window.scope, evidence.workflow.plan_revision, evidence.workflow.current,
                 evidence.execution_sequence, len(evidence.experiment_records),
                 json.dumps(evidence.workflow.pending_experiment, sort_keys=True),
                 json.dumps(evidence.workflow.review_request, sort_keys=True),
                 json.dumps(evidence.environment_revisions, sort_keys=True),
                 json.dumps(evidence.imports, sort_keys=True), json.dumps(evidence.confirmed, sort_keys=True))
        if state != self.state:
            self.state, self.seen = state, {p['call_id'] for p in pages}
            self.feedback, self.feedback_calls = None, set()
            return
        fresh = [p for p in pages if p['call_id'] not in self.seen]
        self.seen.update(p['call_id'] for p in pages)
        if (evidence.pending or evidence.executions or evidence.inline_pending or evidence.skill_operations):
            self.feedback, self.feedback_calls = None, set()
            return  # Waiting for native work is not a retrieval loop.
        if not fresh:
            return  # Re-preparing a request or retrying transport is not a new read.
        visible = {}
        for page in window.visible_reads:
            visible.setdefault(_key(page), []).append((page['offset'], page['end']))
        incoming = {}
        for page in fresh:
            incoming.setdefault(_key(page), []).append((page['offset'], page['end']))
        new_chars = 0
        for key, ranges in incoming.items():
            prior = _merged(visible.get(key, []))
            for start, end in _merged(ranges):
                covered = sum(max(0, min(end, right) - max(start, left)) for left, right in prior)
                new_chars += end - start - covered
        if new_chars or any(_key(page) not in visible for page in fresh):
            self.feedback, self.feedback_calls = None, set()
            return  # New pages, changed versions and context restoration are valid.
        # An additional duplicate after the nudge was actually present in a
        # prepared request is a stronger signal. It is advisory, never an
        # automatic failure or a forced change of scientific method.
        offered = self.feedback_calls <= {p['call_id'] for p in window.visible_reads} if self.feedback_calls else False
        status = 'needs_plan_review' if self.feedback and offered else 'repeated_visible_read'
        self.feedback = {
            'status': status, 'new_visible_chars': 0,
            'scope': 'Same saved source/result version and unchanged execution/plan evidence',
            'reads': [{k: p[k] for k in ('tool', 'call_id', 'source', 'offset', 'end')} for p in fresh[-8:]],
            'instruction': (
                'These ranges were already visible in the preceding prepared model request; rereading them added no information. '
                'Use the available evidence for the pending action, or locate a specific missing region with query/symbol/start_line. '
                'If the method is blocked, explain the missing fact or request plan review. '
                'This signal does not fail the task, prohibit justified reads, or change scientific acceptance.')}
        self.feedback_calls = {p['call_id'] for p in fresh}
        evidence.notice(evidence._context_role, 'Repeated reads added no visible information',
                        workflow_event='retrieval_progress', retrieval_status=status, new_visible_chars=0)
