import socket
import threading

import uvicorn

from .app import create_app


class Service:
    def __init__(self):
        self.server = None
        self.thread = None
        self.error = None

    @property
    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    @property
    def ready(self):
        return self.alive and self.server is not None and self.server.started and not self.server.should_exit

    def start(self, config, directory):
        if self.alive:
            raise ValueError('服务正在运行或停止中')
        settings = config.settings(directory)
        settings.validate()
        if config.mode == 'windows':
            from .windows import printers
            if config.printer not in printers():
                raise ValueError('找不到所选打印机，请刷新列表后重新选择')
        app = create_app(settings)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(('0.0.0.0' if config.lan else '127.0.0.1', config.port))
        except OSError:
            sock.close()
            raise ValueError('无法监听该端口：可能已被占用或被系统限制，请更换端口') from None
        self.error = None
        self.server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_config=None,
                                                  proxy_headers=False, loop='asyncio', http='h11',
                                                  ws='none', timeout_graceful_shutdown=150))

        def run():
            try:
                self.server.run(sockets=[sock])
                if not self.server.started:
                    self.error = '服务未能启动，请检查任务目录是否被另一个实例占用'
            except BaseException:
                self.error = '服务运行异常，请停止其他实例后重试'
            finally:
                sock.close()

        self.thread = threading.Thread(target=run, name='PrintBridge-server', daemon=False)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.should_exit = True


def lan_addresses(port):
    try:
        addresses = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        addresses = []
    return [f'http://{address}:{port}' for address in sorted(set(addresses))
            if not address.startswith('127.') and address != '0.0.0.0']
