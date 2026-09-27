"""The LLM proposes only bounded IDs; both arms retain identical evaluation budgets."""
import asyncio
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.agents.models import AgentConfig
from oaw_xrd.multiphase_harness import MultiphaseConfig, validate_proposal
from oaw_xrd.multiphase_snapshot import normalize_trial


def test_proposals_cannot_escape_pool_or_repeat():
    pool = ['a', 'b', 'c']
    assert validate_proposal('```json\n{"candidate_ids":["b","a"],"reason":"互补峰"}\n```', pool, 2) == (['a', 'b'], '互补峰')
    for value in ({'candidate_ids': ['foreign'], 'reason': 'x'},
                  {'candidate_ids': ['a', 'a'], 'reason': 'x'},
                  {'candidate_ids': ['a', 'b', 'c'], 'reason': 'x'},
                  {'candidate_ids': ['a'], 'reason': 'x', 'code': 'execute me'}):
        with pytest.raises(ValueError):
            validate_proposal(value, pool, 2)
    with pytest.raises(ValueError, match='already'):
        validate_proposal({'candidate_ids': ['b', 'a'], 'reason': 'x'}, pool, 2, [('a', 'b')])


def test_config_prevents_unbounded_budget():
    with pytest.raises(ValueError):
        MultiphaseConfig(budget=1000)
    with pytest.raises(ValueError):
        MultiphaseConfig(max_phases=20)


def test_plot_contributions_preserve_scale_and_identity():
    trial = normalize_trial({'candidate_ids': ['a'], 'status': 'completed', 'quality_score': 81,
        'profiles': {'observed': [[1, 12]], 'calculated': [[1, 11]], 'phases': {'a': [[1, 7]]}}},
        arm='llm', iteration=1, reason='test', labels={'a': 'COD a'})
    assert trial['score'] == 81
    assert trial['plot']['contributions'] == [{'candidate_id': 'a', 'label': 'COD a', 'points': [[1, 7]]}]
