"""Exercise the simulation server using macOS's actual CUPS client; never prints."""
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pypdf import PdfWriter
import uvicorn
from printbridge.app import create_app
from printbridge.config import Settings


def main():
    with tempfile.TemporaryDirectory(prefix='printbridge-ipp-') as directory:
        root = Path(directory)
        app = create_app(Settings(runtime=root, mode='simulate', ipp_enabled=True))
        server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='error', proxy_headers=False))
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            if not server.started:
                raise RuntimeError('Simulation server did not start')
            key = app.state.access.issue('Disposable CUPS smoke test')
            uri = f'ipp://printbridge:{key["secret"]}@127.0.0.1:{port}/ipp/print'
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            document = root / 'test.pdf'
            with document.open('wb') as stream:
                writer.write(stream)
            for test, extra in [('get-printer-attributes.test', []), ('print-job.test', ['-f', str(document)])]:
                result = subprocess.run(['/usr/bin/ipptool', '-t', '-T', '10', *extra, uri,
                                         '/usr/share/cups/ipptool/' + test], capture_output=True, text=True, timeout=20)
                # Never echo a credential-bearing URI from the native client's diagnostic output.
                if result.returncode:
                    output = (result.stdout + result.stderr).replace(key['secret'], '[redacted]')
                    raise RuntimeError(output)
                print(f'CUPS {test}: PASS')
            for _ in range(100):
                jobs = app.state.store.ipp_list(key['id'])
                if jobs and jobs[0]['status'] == 'simulated':
                    break
                time.sleep(.05)
            assert len(jobs) == 1 and jobs[0]['status'] == 'simulated'
            print('CUPS upload -> shared queue -> simulated: PASS')
        finally:
            server.should_exit = True
            thread.join(5)
            sock.close()


if __name__ == '__main__':
    main()
