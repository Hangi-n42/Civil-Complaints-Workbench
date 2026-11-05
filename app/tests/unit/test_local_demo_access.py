from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.mark.parametrize('peer,host,origin', [
    ('192.0.2.10', '127.0.0.1', None),
    ('127.0.0.1', 'external.invalid', None),
    ('127.0.0.1', 'external.invalid', 'http://external.invalid'),
    ('127.0.0.1', '127.0.0.1', 'https://external.invalid'),
    ('127.0.0.1', '127.0.0.1', 'null'),
    ('127.0.0.1', '127.0.0.1', 'http://localhost.external.invalid'),
    ('127.0.0.1', '127.0.0.1', 'http://127.0.0.1@external.invalid'),
])
def test_nonlocal_request_is_denied_before_any_mutation(peer, host, origin, monkeypatch):
    from app.api.routers import retrieval
    called = []
    monkeypatch.setattr(retrieval, 'get_retrieval_service', lambda: called.append(True))
    headers = {'Origin': origin} if origin else {}
    response = TestClient(app, base_url=f'http://{host}', client=(peer, 50000)).post(
        '/api/v1/index', headers=headers, json={'cases': [{'case_id': 'ONLY-SYNTHETIC'}]},
    )
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'LOCAL_ACCESS_ONLY'
    assert called == []


@pytest.mark.parametrize('host,peer,origin', [
    ('127.0.0.1', '127.0.0.1', None),
    ('localhost', '127.0.0.1', 'http://localhost:3092'),
    ('127.0.0.1', '127.0.0.1', 'http://127.0.0.1:3092'),
    ('[::1]', '::1', 'http://[::1]:3092'),
])
def test_local_health_is_preserved(host, peer, origin):
    headers = {'Host': host, **({'Origin': origin} if origin else {})}
    response = TestClient(app, base_url='http://127.0.0.1', client=(peer, 50000)).get('/health', headers=headers)
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'
    if origin:
        assert response.headers['access-control-allow-origin'] == origin


def test_cross_origin_preflight_is_not_authorized():
    response = TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)).options(
        '/api/v1/index', headers={'Origin': 'http://external.invalid', 'Access-Control-Request-Method': 'POST'},
    )
    assert response.status_code == 403
    assert 'access-control-allow-origin' not in response.headers


@pytest.mark.parametrize('host', ['0.0.0.0', '::', '192.0.2.1', 'external.invalid', ''])
def test_nonlocal_bind_is_rejected(host):
    from app.core.local_access import require_loopback_host
    with pytest.raises(ValueError, match='로컬 시연'):
        require_loopback_host(host)


@pytest.mark.parametrize('host', ['127.0.0.1', 'localhost', '::1'])
def test_local_bind_is_preserved(host):
    from app.core.local_access import require_loopback_host
    assert require_loopback_host(host) == host
