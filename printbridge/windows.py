import subprocess
import sys


RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'PrintBridge'


def printers():
    import win32print
    entries = win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS, None, 4)
    return sorted({entry['pPrinterName'] for entry in entries}, key=str.casefold)


def startup_command(executable):
    command = subprocess.list2cmdline([str(executable), '--background'])
    if len(command) > 260:
        raise ValueError('程序路径过长，无法设置登录启动；请移至较短路径')
    return command


def set_autostart(enabled, executable=None):
    import winreg
    if enabled and not getattr(sys, 'frozen', False):
        raise ValueError('登录启动仅适用于打包后的 PrintBridge.exe')
    command = startup_command(executable or sys.executable) if enabled else None
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass
