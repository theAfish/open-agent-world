from oaw_xrd import multiphase_wpem as wpem


def test_shared_incumbent_is_reviewed_once_and_negative_removal_is_preserved(tmp_path, monkeypatch):
    calls = []
    def fit(payload, combo, output, root, iterations):
        calls.append(list(combo))
        return {'status': 'completed', 'candidate_ids': list(combo), 'directory': str(tmp_path),
                'metrics': {'rwp_percent': 30 if len(combo) == 2 else 25}}
    monkeypatch.setattr(wpem, 'fit_combination', fit)
    result = wpem.review_combinations({}, [['b', 'a'], ['a', 'b']], tmp_path, tmp_path)
    assert calls == [['a', 'b'], ['b'], ['a']]
    assert [row['delta_rwp'] for row in result['reviews'][0]['removals']] == [-5, -5]
    assert 'mass_fraction' not in str(result)


def test_failed_joint_fit_retains_failure_without_inventing_removal_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(wpem, 'fit_combination', lambda *args: {'status': 'failed', 'error': 'invalid CIF'})
    result = wpem.review_combinations({}, [['a', 'b']], tmp_path, tmp_path)
    assert result['status'] == 'failed'
    assert result['reviews'][0]['full']['error'] == 'invalid CIF'
    assert result['reviews'][0]['removals'] == []
