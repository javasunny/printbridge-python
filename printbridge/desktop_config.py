import json
import os
import shutil
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .config import Settings


def user_directory():
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        raise ValueError('无法确定 Windows 用户数据目录')
    return Path(base) / 'PrintBridge'


@dataclass(frozen=True)
class DesktopConfig:
    mode: str = 'simulate'
    printer: str = ''
    sumatra: str = ''
    lan: bool = False
    port: int = 8765
    ipp_enabled: bool = True
    autostart: bool = False

    def validate(self):
        if self.mode not in {'simulate', 'windows'}:
            raise ValueError('请选择模拟模式或真实打印模式')
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ValueError('端口须为 1024–65535 之间的整数')
        if not isinstance(self.printer, str) or not isinstance(self.sumatra, str):
            raise ValueError('打印机和程序路径必须是文本')
        if any(type(v) is not bool for v in (self.lan, self.ipp_enabled, self.autostart)):
            raise ValueError('无效的开关配置')

    def settings(self, directory):
        self.validate()
        # A queued simulation must never become a real print after changing mode.
        return Settings(runtime=directory / ('runtime-' + self.mode), mode=self.mode,
                        printer=self.printer, sumatra=self.sumatra, ipp_enabled=self.ipp_enabled)


def load_config(directory):
    path = directory / 'desktop.json'
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or set(data) - {f.name for f in fields(DesktopConfig)}:
            raise ValueError()
        config = DesktopConfig(**data)
        config.validate()
        return config
    except (ValueError, TypeError):
        raise ValueError('本机配置文件无效；请在配置窗口重新保存。原文件不会自动删除。') from None


def save_config(directory, config):
    config.validate()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'desktop.json'
    temporary = directory / 'desktop.json.tmp'
    temporary.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)


def detect_sumatra():
    candidates = []
    found = shutil.which('SumatraPDF.exe')
    if found:
        candidates.append(Path(found))
    for variable in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        base = os.environ.get(variable)
        if base:
            candidates.append(Path(base) / 'SumatraPDF' / 'SumatraPDF.exe')
            candidates.append(Path(base) / 'Programs' / 'SumatraPDF' / 'SumatraPDF.exe')
    return next((str(p) for p in candidates if p.is_file()), '')
