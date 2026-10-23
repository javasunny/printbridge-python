from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from printbridge.desktop_config import DesktopConfig, detect_sumatra, load_config, save_config
from printbridge.desktop_service import Service
from printbridge.windows import startup_command, set_autostart


def test_config_persistence_and_separate_modes(tmp_path):
    assert load_config(tmp_path) is None
    config = DesktopConfig(printer='东芝', sumatra='C:/Apps/SumatraPDF.exe', lan=True, ipp_enabled=True)
    save_config(tmp_path, config)
    assert load_config(tmp_path) == config
    assert not (tmp_path / 'desktop.json.tmp').exists()
    assert config.settings(tmp_path).runtime != replace(config, mode='windows').settings(tmp_path).runtime
    (tmp_path / 'desktop.json').write_text('{bad', encoding='utf-8')
    with pytest.raises(ValueError, match='配置文件无效'):
        load_config(tmp_path)
    assert (tmp_path / 'desktop.json').read_text() == '{bad'


@pytest.mark.parametrize('port', [0, 80, 65536, True, '8765'])
def test_invalid_ports(port):
    with pytest.raises(ValueError):
        DesktopConfig(port=port).validate()


def test_detect_sumatra(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.delenv('ProgramFiles', raising=False)
    monkeypatch.delenv('ProgramFiles(x86)', raising=False)
    with patch('printbridge.desktop_config.shutil.which', return_value=None):
        assert detect_sumatra() == ''
        executable = tmp_path / 'SumatraPDF' / 'SumatraPDF.exe'
        executable.parent.mkdir()
        executable.touch()
        assert detect_sumatra() == str(executable)


def test_autostart_only_current_user_and_no_shell():
    command = startup_command('C:\\Program Files\\PrintBridge\\PrintBridge.exe')
    assert command == '"C:\\Program Files\\PrintBridge\\PrintBridge.exe" --background'
    registry = MagicMock()
    with patch.dict('sys.modules', {'winreg': registry}), patch('sys.frozen', True, create=True):
        set_autostart(True, 'C:\\PrintBridge.exe')
        registry.CreateKey.assert_called_once_with(registry.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run')
        assert registry.SetValueEx.call_args.args[-1] == 'C:\\PrintBridge.exe --background'
        set_autostart(False)
        assert registry.DeleteValue.call_args.args[-1] == 'PrintBridge'


def test_service_port_failure_closes_socket(tmp_path):
    sock = MagicMock()
    sock.bind.side_effect = OSError()
    with patch('printbridge.desktop_service.socket.socket', return_value=sock):
        with pytest.raises(ValueError, match='端口'):
            Service().start(DesktopConfig(), tmp_path)
    sock.close.assert_called_once()


def test_service_does_not_start_if_printer_missing(tmp_path):
    config = DesktopConfig(mode='windows', printer='missing', sumatra='x.exe')
    with patch('printbridge.config.Settings.validate'), patch('printbridge.windows.printers', return_value=[]):
        with pytest.raises(ValueError, match='找不到'):
            Service().start(config, tmp_path)


def test_service_start_stop_uses_bound_socket(tmp_path):
    service = Service()
    socket = MagicMock()
    server = MagicMock()
    thread = MagicMock()
    thread.is_alive.return_value = False
    with patch('printbridge.desktop_service.socket.socket', return_value=socket), \
         patch('printbridge.desktop_service.uvicorn.Server', return_value=server), \
         patch('printbridge.desktop_service.threading.Thread', return_value=thread) as thread_type:
        service.start(DesktopConfig(lan=True), tmp_path)
        socket.bind.assert_called_once_with(('0.0.0.0', 8765))
        thread_type.call_args.kwargs['target']()
        server.run.assert_called_once_with(sockets=[socket])
        socket.close.assert_called_once()
        service.stop()
        assert server.should_exit is True
