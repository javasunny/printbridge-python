import hashlib
import time
from uuid import uuid4

from fastapi.testclient import TestClient

from printbridge.app import create_app
from printbridge.config import Settings


def local_client(app):
    return TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))


def issue(client):
    csrf = client.get('/admin/api/session').json()['csrf']
    admin_headers = {'Origin': 'http://127.0.0.1:8765', 'X-Admin-CSRF': csrf}
    response = client.post('/admin/api/keys', headers=admin_headers, json={'name': 'Office Mac'})
    assert response.status_code == 201
    return response.json(), admin_headers


def test_key_lifecycle_presence_and_hash_only(tmp_path):
    app = create_app(Settings(runtime=tmp_path))
    with local_client(app) as client:
        key, admin_headers = issue(client)
        auth = {'Authorization': 'Bearer ' + key['secret']}
        client_id = str(uuid4())
        assert client.get('/api/settings', headers=auth).status_code == 200
        assert client.post('/api/heartbeat', headers=auth, json={'client_id': client_id}).status_code == 200
        clients = client.get('/admin/api/clients').json()
        assert clients[0]['online'] is True
        assert clients[0]['ip'] == '127.0.0.1'
        listing = client.get('/admin/api/keys')
        assert key['secret'] not in listing.text and 'digest' not in listing.text
        with app.state.store.connect() as con:
            row = con.execute('SELECT * FROM access_keys').fetchone()
            assert row['digest'] == hashlib.sha256(key['secret'].encode()).hexdigest()
            assert key['secret'] not in str(tuple(row))
            con.execute('UPDATE clients SET last_seen=?', (time.time() - 61,))
        assert client.get('/admin/api/clients').json()[0]['online'] is False
        client.post('/api/heartbeat', headers=auth, json={'client_id': client_id})
        client.post('/api/disconnect', headers=auth, json={'client_id': client_id})
        assert client.get('/admin/api/clients').json()[0]['online'] is False
        assert client.delete('/admin/api/keys/' + key['id'], headers=admin_headers).status_code == 200
        assert client.get('/api/jobs', headers=auth).status_code == 401
        assert client.post('/api/jobs', headers=auth, content=b'PDF').status_code == 401
        assert client.post('/api/heartbeat', headers=auth, json={'client_id': client_id}).status_code == 401
    with local_client(create_app(Settings(runtime=tmp_path))) as restarted:
        assert restarted.get('/api/settings', headers=auth).status_code == 401
        assert restarted.get('/admin/api/keys').json()[0]['revoked_at'] is not None


def test_admin_rejects_remote_forged_and_cross_site_requests(tmp_path):
    app = create_app(Settings(runtime=tmp_path))
    with local_client(app) as client:
        for path in ['/admin', '/admin/api/keys', '/admin/api/clients', '/admin/api/session']:
            assert client.get(path, headers={'Host':'attacker.example'}).status_code == 403
            assert client.get(path, headers={'Origin':'http://attacker.example'}).status_code == 403
        assert client.post('/admin/api/keys', json={'name':'Denied'}).status_code == 403
        csrf = client.get('/admin/api/session').json()['csrf']
        assert client.post('/admin/api/keys', headers={'X-Admin-CSRF':csrf, 'Origin':'http://attacker.example'}, json={'name':'Denied'}).status_code == 403
        assert client.get('/admin/api/keys').json() == []
        assert client.get('/admin').headers['cache-control'] == 'no-store'
        assert client.get('/admin').status_code == 200
        assert client.get('/static/admin.js').status_code == 200
        assert client.get('/static/admin.html').status_code == 404
    with TestClient(app, base_url='http://127.0.0.1:8765', client=('192.168.1.22', 12345)) as remote:
        for path in ['/admin', '/admin/api/session', '/admin/api/keys', '/admin/api/clients']:
            assert remote.get(path, headers={'X-Forwarded-For':'127.0.0.1'}).status_code == 403


def test_issued_key_survives_restart_clients_start_offline(tmp_path):
    app = create_app(Settings(runtime=tmp_path))
    with local_client(app) as client:
        key, _ = issue(client)
        auth = {'Authorization': 'Bearer ' + key['secret']}
        for _ in range(2):
            client.post('/api/heartbeat', headers=auth, json={'client_id':str(uuid4())})
        assert len(client.get('/admin/api/clients').json()) == 2
    with local_client(create_app(Settings(runtime=tmp_path))) as client:
        assert client.get('/api/settings', headers=auth).status_code == 200
        assert all(not c['online'] for c in client.get('/admin/api/clients').json())
