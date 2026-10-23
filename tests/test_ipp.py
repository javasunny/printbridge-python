import base64
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from printbridge.app import create_app
from printbridge.config import Settings
from printbridge.ipp import attr, media_collection
from printbridge.ipp_codec import Group, decode, encode


def pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def payload(code=11, request_id=1, operation=(), job=(), document=b''):
    attrs = [attr('attributes-charset', 0x47, 'utf-8'), attr('attributes-natural-language', 0x48, 'en'),
             attr('printer-uri', 0x45, 'ipp://testserver/ipp/print'), *operation]
    return encode((1, 1), code, request_id, [Group(1, attrs)] + ([Group(2, list(job))] if job else [])) + document


def send(client, content, secret=None):
    headers = {'Content-Type': 'application/ipp'}
    if secret:
        headers['Authorization'] = 'Basic ' + base64.b64encode(('printbridge:' + secret).encode()).decode()
    return client.post('/ipp/print', content=content, headers=headers)


def values(message):
    return {a.name: a.values for g in message.groups for a in g.attributes}


@pytest.fixture
def bridge(tmp_path):
    app = create_app(Settings(runtime=tmp_path, ipp_enabled=True, max_upload_mb=1))
    with TestClient(app) as client:
        key = app.state.access.issue('IPP test')
        yield client, key


def test_capabilities_and_auth(bridge):
    client, key = bridge
    response = send(client, payload())
    assert response.status_code == 200
    version2 = b'\x02\x00' + payload()[2:]
    assert decode(send(client, version2).content).version == (2, 0)
    attrs = values(decode(response.content))
    assert attrs['operations-supported'] == [2, 4, 8, 9, 10, 11]
    assert attrs['document-format-supported'] == ['application/pdf', 'application/octet-stream']
    assert 'ipp-features-supported' not in attrs  # Not advertised as IPP Everywhere/AirPrint.
    assert send(client, payload(2, document=pdf())).status_code == 401
    assert send(client, payload(10), 'invalid').status_code == 401
    client.app.state.access.revoke(key['id'])
    assert send(client, payload(10), key['secret']).status_code == 401


def test_print_duplicate_status_and_ownership(bridge):
    client, key = bridge
    body = payload(2, operation=[attr('job-name', 0x42, '中文测试')],
                   job=[attr('copies', 0x21, 2), attr('sides', 0x44, 'two-sided-long-edge'),
                        attr('media-col', 0x34, media_collection())], document=pdf())
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda _: decode(send(client, body, key['secret']).content), range(3)))
    assert all(r.code == 0 for r in responses)
    numbers = {values(r)['job-id'][0] for r in responses}
    assert len(numbers) == 1
    number = numbers.pop()
    job = client.app.state.store.ipp_job(key['id'], number=number)
    assert job['copies'] == 2 and job['sides'] == 'duplexlong'
    assert len(client.app.state.store.list()) == 1
    second = client.app.state.access.issue('Other device')
    query = payload(9, operation=[attr('job-id', 0x21, number)])
    assert decode(send(client, query, second['secret']).content).code == 0x0406
    assert len(decode(send(client, payload(10), second['secret']).content).groups) == 1
    # Distinct request IDs intentionally create distinct jobs.
    assert decode(send(client, payload(2, request_id=2, document=pdf()), key['secret']).content).code == 0
    client.app.state.store.finish(job['id'], 'submitted', 'ok')
    result = values(decode(send(client, query, key['secret']).content))
    assert result['job-state'] == [9] and result['job-state-reasons'] == ['queued-in-device']
    client.app.state.store.finish(job['id'], 'uncertain', 'unknown')
    assert values(decode(send(client, query, key['secret']).content))['job-state'] == [8]


def test_reject_invalid_and_unsupported(bridge):
    client, key = bridge
    def code(data):
        return decode(send(client, data, key['secret']).content).code
    assert code(payload(2, document=b'%PDF-fake')) == 0x0411
    assert code(payload(2, document=b'x' * (1024 * 1024 + 1))) == 0x0408
    assert code(payload(2, operation=[attr('document-format', 0x49, 'application/postscript')])) == 0x040A
    for option in [attr('copies', 0x21, 0), attr('sides', 0x44, 'bad'), attr('media', 0x44, 'na_letter_8.5x11in'),
                   attr('page-ranges', 0x33, (1, 2))]:
        assert code(payload(4, job=[option])) == 0x040B
    assert code(payload(5)) == 0x0501  # Create-Job intentionally unsupported.
    assert send(client, b'bad').status_code == 400
    assert send(client, b'\1\1\0\x0b\0\0\0\1\1' + b'\x41\xff\xff' + b'x' * 65536).status_code == 400
    assert not list(client.app.state.store.root.glob('*.pdf'))
    assert not list(client.app.state.store.root.glob('*.part'))


def test_cancel_only_waiting_job(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def printer(*args):
        entered.set()
        release.wait(5)
        return 'simulated', 'test'
    app = create_app(Settings(runtime=tmp_path, ipp_enabled=True), printer=printer)
    with TestClient(app) as client:
        key = app.state.access.issue('test')
        try:
            first = decode(send(client, payload(2, request_id=1, document=pdf()), key['secret']).content)
            assert entered.wait(2)
            number = values(first)['job-id'][0]
            cancel = lambda n: payload(8, operation=[attr('job-id', 0x21, n)])
            assert decode(send(client, cancel(number), key['secret']).content).code == 0x0404
            second = decode(send(client, payload(2, request_id=2, document=pdf()), key['secret']).content)
            number2 = values(second)['job-id'][0]
            assert decode(send(client, cancel(number2), key['secret']).content).code == 0
            assert app.state.store.ipp_job(key['id'], number=number2)['status'] == 'canceled'
        finally:
            release.set()


def test_default_disabled(tmp_path):
    with TestClient(create_app(Settings(runtime=tmp_path))) as client:
        assert send(client, payload()).status_code == 404
