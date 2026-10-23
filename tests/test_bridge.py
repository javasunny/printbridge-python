import io
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from printbridge.app import create_app
from printbridge.config import Settings
from printbridge.printer import command, print_pdf
from printbridge.store import Store

TOKEN = "pb_test-token-abcdefghijklmnopqrstuvwxyz"


def pdf():
    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(buffer)
    return buffer.getvalue()


def headers(key=None):
    return {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/pdf",
            "Idempotency-Key": key or str(uuid4())}


def seed_key(client):
    with patch("printbridge.access.secrets.token_urlsafe", return_value=TOKEN[3:]):
        client.app.state.access.issue("Test device")


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(runtime=tmp_path, max_upload_mb=1))) as client:
        seed_key(client)
        yield client


def test_auth_and_validation(client):
    assert client.get('/api/jobs').status_code == 401
    assert client.post('/api/jobs', content=pdf()).status_code == 401
    assert client.get('/api/settings', headers=headers()).json()['mode'] == 'simulate'
    assert client.post('/api/jobs', headers=headers(), content=b'%PDF-fake').status_code == 400
    assert client.post('/api/jobs', headers=headers(), content=b'x' * (1024 * 1024 + 1)).status_code == 413
    assert client.post('/api/jobs?copies=0', headers=headers(), content=pdf()).status_code == 422
    assert client.post('/api/jobs?sides=bad', headers=headers(), content=pdf()).status_code == 422
    assert client.post('/api/jobs', headers={**headers(), 'Idempotency-Key': 'bad'}, content=pdf()).status_code == 400
    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    writer.encrypt('secret')
    stream = io.BytesIO()
    writer.write(stream)
    assert client.post('/api/jobs', headers=headers(), content=stream.getvalue()).status_code == 400
    assert not list(client.app.state.store.root.glob('*.part'))
    assert not list(client.app.state.store.root.glob('*.pdf'))


def test_idempotency_and_worker(client):
    key = str(uuid4())
    body = pdf()
    first = client.post('/api/jobs?name=../../private.pdf', headers=headers(key), content=body)
    assert first.status_code == 201
    job = first.json()
    assert job['name'] == 'private.pdf'
    retry = client.post('/api/jobs', headers=headers(key), content=body)
    assert retry.json()['id'] == job['id']
    assert client.post('/api/jobs?copies=2', headers=headers(key), content=body).status_code == 409
    for _ in range(40):
        rows = client.get('/api/jobs', headers=headers()).json()
        if rows[0]['status'] == 'simulated':
            break
        time.sleep(.05)
    assert len(rows) == 1
    assert rows[0]['status'] == 'simulated'
    assert len(list(client.app.state.store.root.glob('*.pdf'))) == 1
    assert client.get('/runtime/jobs.sqlite3').status_code == 404


def test_concurrent_duplicate(client):
    key, body = str(uuid4()), pdf()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: client.post('/api/jobs', headers=headers(key), content=body), range(4)))
    assert all(r.status_code == 201 for r in results)
    assert len({r.json()['id'] for r in results}) == 1


def test_recovery_and_cleanup(tmp_path):
    store = Store(tmp_path)
    store.initialize()
    for job_id in ('active', 'waiting', 'done'):
        store.add(job_id, job_id, job_id, 'a.pdf', 1, 'simplex')
        (tmp_path / (job_id + '.pdf')).write_bytes(pdf())
    assert store.claim()['id'] == 'active'
    store.finish('done', 'submitted', 'ok')
    store.initialize()
    states = {j['id']: j['status'] for j in store.list()}
    assert states == {'active': 'uncertain', 'waiting': 'queued', 'done': 'submitted'}
    with store.connect() as con:
        con.execute('UPDATE jobs SET updated_at=0')
    store.cleanup(1)
    assert (tmp_path / 'waiting.pdf').exists()
    assert not (tmp_path / 'done.pdf').exists()
    assert len(store.list()) == 3  # Retain request keys even after document cleanup.


def test_print_command_and_uncertainty(tmp_path):
    settings = Settings(mode='windows', printer='TOSHIBA printer', sumatra='C:/SumatraPDF.exe')
    job = {'copies': 2, 'sides': 'duplexlong'}
    args = command(settings, job, tmp_path / 'a.pdf')
    assert args[args.index('-print-to') + 1] == 'TOSHIBA printer'
    assert 'paper=A4,fit,monochrome,duplexlong,2x' in args
    with patch('printbridge.printer.subprocess.run', side_effect=subprocess.TimeoutExpired(args, 120)):
        assert print_pdf(settings, job, tmp_path / 'a.pdf')[0] == 'uncertain'
    with patch('printbridge.printer.subprocess.run', side_effect=OSError()):
        assert print_pdf(settings, job, tmp_path / 'a.pdf')[0] == 'failed'
    with patch('printbridge.printer.subprocess.run', return_value=subprocess.CompletedProcess(args, 0)) as run:
        assert print_pdf(settings, job, tmp_path / 'a.pdf')[0] == 'submitted'
        assert run.call_args.kwargs.get('shell', False) is False


def test_printer_exception_is_not_retried(tmp_path):
    calls = []
    def broken(*args):
        calls.append(1)
        raise RuntimeError('private error')
    with TestClient(create_app(Settings(runtime=tmp_path), printer=broken)) as client:
        seed_key(client)
        client.post('/api/jobs', headers=headers(), content=pdf())
        for _ in range(40):
            rows = client.get('/api/jobs', headers=headers()).json()
            if rows[0]['status'] == 'uncertain':
                break
            time.sleep(.05)
        assert rows[0]['status'] == 'uncertain'
        assert 'private' not in rows[0]['message']
    assert calls == [1]
