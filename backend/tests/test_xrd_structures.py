"""Candidate structure retrieval preserves source and stays local after caching."""
import asyncio
import base64
import hashlib
from pathlib import Path

import httpx
import pytest

from backend.tests.conftest import create_node
from backend.errors import ResourceValidationError
from oaw_xrd import structures


CIF = b'''data_7222155
_cell_length_a 5.43
_cell_length_b 5.43
_cell_length_c 5.43
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Si1 Si 0 0 0
'''


def transport(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(structures.httpx, 'AsyncClient',
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))


def test_fetch_persists_cif_and_offline_cache_and_download(client, tmp_path, monkeypatch):
    monkeypatch.setenv('OAW_XRD_STRUCTURE_ROOT', str(tmp_path))
    requests = []
    def remote(request):
        requests.append(request)
        assert request.method == 'GET' and request.content == b''
        assert str(request.url) == 'https://www.crystallography.net/cod/7222155.cif'
        return httpx.Response(200, content=CIF)
    transport(monkeypatch, remote)
    card = create_node(client, 'xrd.match')
    url = f"/api/nodes/{card['id']}"
    initial = client.get(url + '/document').json()
    response = client.post(url + '/actions/fetch_cod', json={
        'expected_revision': initial['revision'], 'arguments': {'cod_id': '7222155'}})
    assert response.status_code == 200, response.text
    doc = response.json()
    structure = doc['value']['structure']
    assert base64.b64decode(structure['source_base64']) == CIF
    assert structure['sha256'] == hashlib.sha256(CIF).hexdigest()
    assert Path(structure['local_path']).read_bytes() == CIF
    assert Path(structure['local_path']).is_relative_to(tmp_path)
    assert structure['metadata']['association'] == 'candidate_only'
    assert client.get(url + '/document').json()['value'] == doc['value']
    assert client.get(url + '/document/downloads/structure').content == CIF
    assert client.get(url).json()['config']['mode'] == 'match'
    response = client.post(url + '/actions/fetch_cod', json={
        'expected_revision': doc['revision'], 'arguments': {'cod_id': '7222155'}})
    assert response.status_code == 200 and len(requests) == 1
    # Cache tampering cannot be represented as the preserved original.
    Path(structure['local_path']).write_bytes(b'tampered')
    recovered = asyncio.run(structures.prepare_cod_structure({}, {'cod_id': '7222155'}))
    assert len(requests) == 2 and base64.b64decode(recovered['structure']['source_base64']) == CIF


@pytest.mark.parametrize('bad', ['../../secret', 'https://evil.test/file', '7222155?x=1', 7222155, True, '123'])
def test_only_cod_ids_are_accepted_before_any_network(bad):
    with pytest.raises(ResourceValidationError, match='7 位'):
        asyncio.run(structures.prepare_cod_structure({}, {'cod_id': bad}))


@pytest.mark.parametrize('raw', [b'<html>Not a CIF</html>', CIF.replace(b'data_7222155', b'data_1000000'),
                               CIF.replace(b'_atom_site_fract_x', b'_wrong_tag'), CIF + b'\x00'])
def test_invalid_cif_never_creates_a_cache(raw, tmp_path, monkeypatch):
    monkeypatch.setenv('OAW_XRD_STRUCTURE_ROOT', str(tmp_path))
    transport(monkeypatch, lambda req: httpx.Response(200, content=raw))
    with pytest.raises(ResourceValidationError):
        asyncio.run(structures.prepare_cod_structure({}, {'cod_id': '7222155'}))
    assert list(tmp_path.iterdir()) == []


def test_redirect_cannot_read_local_or_foreign_endpoints(monkeypatch):
    seen = []
    def response(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={'Location': 'http://127.0.0.1:5173/api/world'})
    transport(monkeypatch, response)
    with pytest.raises(ResourceValidationError, match='重定向'):
        asyncio.run(structures.download_cif('7222155'))
    assert len(seen) == 1


def test_size_limits_and_http_failures_are_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv('OAW_XRD_STRUCTURE_ROOT', str(tmp_path))
    responses = [httpx.Response(200, headers={'content-length': str(structures.MAX_CIF_BYTES + 1)}),
                 httpx.Response(200, content=b'x' * (structures.MAX_CIF_BYTES + 1)),
                 httpx.Response(404)]
    transport(monkeypatch, lambda req: responses.pop(0))
    for text in ('2 MiB', '2 MiB', 'HTTP 404'):
        with pytest.raises(ResourceValidationError, match=text):
            asyncio.run(structures.prepare_cod_structure({}, {'cod_id': '7222155'}))
    assert list(tmp_path.iterdir()) == []


def test_failed_request_preserves_existing_node_document(client, monkeypatch, tmp_path):
    monkeypatch.setenv('OAW_XRD_STRUCTURE_ROOT', str(tmp_path))
    transport(monkeypatch, lambda req: httpx.Response(404))
    card = create_node(client, 'xrd.match')
    url = f"/api/nodes/{card['id']}"
    before = client.get(url + '/document').json()
    response = client.post(url + '/actions/fetch_cod', json={
        'expected_revision': before['revision'], 'arguments': {'cod_id': '7222155'}})
    assert response.status_code == 422, response.text
    assert client.get(url + '/document').json() == before
