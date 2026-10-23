import logging
import os
from queue import Empty, Queue
import sys
import threading
import webbrowser

from filelock import FileLock, Timeout

from .desktop_config import DesktopConfig, detect_sumatra, load_config, save_config, user_directory
from .desktop_service import Service, lan_addresses
from .windows import printers, set_autostart


class Desktop:
    def __init__(self, root, directory, config, background=False):
        import tkinter as tk
        from tkinter import ttk
        self.root, self.directory = root, directory
        self.service = Service()
        self.events = Queue()
        self.tray = None
        self.tray_ready = False
        self.quitting = False
        self.running = None
        self.phase = 'stopped'
        self.background = background
        value = config or DesktopConfig(sumatra=detect_sumatra())
        root.title('PrintBridge · 打印中转站')
        root.geometry('700x640')
        root.minsize(650, 600)
        root.protocol('WM_DELETE_WINDOW', self.hide)
        panel = ttk.Frame(root, padding=24)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='PrintBridge 打印中转站', font=('Microsoft YaHei UI', 19, 'bold')).pack(anchor='w')
        ttk.Label(panel, text='选择打印机，启动后即可从 Mac 网页或打印窗口发送任务。').pack(anchor='w', pady=(6, 18))
        self.form = ttk.Frame(panel)
        self.form.pack(fill='x')
        self.form.columnconfigure(1, weight=1)
        self.mode = tk.StringVar(value='模拟模式（不出纸）' if value.mode == 'simulate' else '真实打印')
        self.printer = tk.StringVar(value=value.printer)
        self.sumatra = tk.StringVar(value=value.sumatra)
        self.port = tk.StringVar(value=str(value.port))
        self.lan = tk.BooleanVar(value=value.lan)
        self.ipp = tk.BooleanVar(value=value.ipp_enabled)
        self.autostart = tk.BooleanVar(value=value.autostart)
        self.widgets = []

        def field(row, label, widget, state='normal'):
            ttk.Label(self.form, text=label).grid(row=row, column=0, sticky='w', pady=8, padx=(0, 12))
            widget.grid(row=row, column=1, sticky='ew', pady=8)
            self.widgets.append((widget, state))

        field(0, '运行模式', ttk.Combobox(self.form, textvariable=self.mode, values=['模拟模式（不出纸）', '真实打印'], state='readonly'), 'readonly')
        self.printer_box = ttk.Combobox(self.form, textvariable=self.printer, state='readonly')
        field(1, 'Windows 打印机', self.printer_box, 'readonly')
        refresh = ttk.Button(self.form, text='刷新', command=self.refresh_printers)
        refresh.grid(row=1, column=2, padx=(8, 0)); self.widgets.append((refresh, 'normal'))
        field(2, 'SumatraPDF', ttk.Entry(self.form, textvariable=self.sumatra))
        browse = ttk.Button(self.form, text='选择文件', command=self.browse)
        browse.grid(row=2, column=2, padx=(8, 0)); self.widgets.append((browse, 'normal'))
        field(3, '服务端口', ttk.Entry(self.form, textvariable=self.port))
        for row, label, variable in [(4, '允许可信局域网访问', self.lan), (5, '启用 IPP（Mac 系统打印）', self.ipp), (6, 'Windows 登录后自动启动', self.autostart)]:
            box = ttk.Checkbutton(self.form, text=label, variable=variable)
            box.grid(row=row, column=1, sticky='w', pady=5)
            self.widgets.append((box, 'normal' if row != 6 or getattr(sys, 'frozen', False) else 'disabled'))
        self.status = tk.StringVar(value='首次使用请检查配置，然后保存并启动。')
        ttk.Label(panel, textvariable=self.status, wraplength=640, foreground='#1b6854').pack(anchor='w', pady=(18, 8))
        self.addresses = tk.Text(panel, height=4, wrap='word', font=('Segoe UI', 10), state='disabled')
        self.addresses.pack(fill='x')
        actions = ttk.Frame(panel); actions.pack(fill='x', pady=12)
        self.start_button = ttk.Button(actions, text='保存并启动', command=self.start)
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(actions, text='停止服务', command=self.stop, state='disabled')
        self.stop_button.pack(side='left', padx=8)
        self.web_button = ttk.Button(actions, text='打开打印页', command=lambda: self.open_page('/'), state='disabled')
        self.web_button.pack(side='left')
        self.admin_button = ttk.Button(actions, text='设备令牌管理', command=lambda: self.open_page('/admin'), state='disabled')
        self.admin_button.pack(side='left', padx=8)
        ttk.Button(actions, text='退出', command=self.quit).pack(side='right')
        ttk.Label(panel, text='关闭窗口会缩至托盘。修改配置前请停止服务。\n局域网访问需放行 Windows 专用网络防火墙；不会自动修改防火墙。', wraplength=640).pack(anchor='w')
        ttk.Button(panel, text='打开配置和数据目录', command=lambda: os.startfile(directory)).pack(anchor='w', pady=8)
        self.refresh_printers()
        self.create_tray()
        root.after(200, self.poll)
        if config:
            root.after(300, lambda: self.start(persist=False))

    def error(self, text):
        from tkinter import messagebox
        self.show()
        self.status.set(text)
        messagebox.showerror('PrintBridge', text, parent=self.root)

    def refresh_printers(self):
        try:
            names = printers()
            self.printer_box.configure(values=names)
            if not self.printer.get() and names:
                self.printer.set(next((n for n in names if 'TOSHIBA' in n.upper()), names[0]))
        except Exception:
            self.status.set('无法读取 Windows 打印机列表，请检查打印服务和驱动后点击刷新。')

    def browse(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, title='选择 SumatraPDF.exe', filetypes=[('Windows 程序', '*.exe')])
        if path:
            self.sumatra.set(path)

    def read_config(self):
        try:
            port = int(self.port.get())
        except ValueError:
            raise ValueError('端口须为整数') from None
        return DesktopConfig(mode='simulate' if self.mode.get().startswith('模拟') else 'windows',
                             printer=self.printer.get(), sumatra=self.sumatra.get().strip(),
                             port=port, lan=self.lan.get(), ipp_enabled=self.ipp.get(), autostart=self.autostart.get())

    def lock_form(self, locked):
        for widget, normal in self.widgets:
            widget.configure(state='disabled' if locked else normal)
        self.start_button.configure(state='disabled' if locked else 'normal')

    def start(self, persist=True):
        if self.service.alive or self.quitting:
            return
        try:
            config = self.read_config()
            self.service.start(config, self.directory)
            if persist:
                set_autostart(config.autostart)
                save_config(self.directory, config)
            self.running = config
            self.phase = 'starting'
            self.lock_form(True)
            self.status.set('正在启动服务…')
        except Exception as exc:
            self.service.stop()
            self.phase = 'stopping' if self.service.alive else 'stopped'
            self.lock_form(self.service.alive)
            self.error(str(exc) if isinstance(exc, ValueError) else '配置保存或启动失败，请检查目录权限与 Windows 设置。')

    def stop(self):
        if self.service.alive:
            self.service.stop()
            self.phase = 'stopping'
            self.status.set('正在停止，等待已开始的处理结束；不会自动重打。')
            self.stop_button.configure(state='disabled')
            self.web_button.configure(state='disabled')
            self.admin_button.configure(state='disabled')

    def open_page(self, path):
        if self.service.ready and self.running:
            webbrowser.open(f'http://127.0.0.1:{self.running.port}{path}')

    def show(self):
        self.root.deiconify()
        self.root.lift()

    def hide(self):
        if self.tray_ready:
            self.root.withdraw()
        else:
            self.status.set('托盘不可用，请最小化窗口，或点击退出。')
            self.root.iconify()

    def quit(self):
        self.quitting = True
        self.show()
        self.lock_form(True)
        self.stop()

    def create_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            image = Image.new('RGB', (64, 64), '#155d49')
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((15, 10, 49, 54), radius=4, fill='white')
            draw.line((22, 26, 42, 26), fill='#155d49', width=3)
            draw.line((22, 36, 42, 36), fill='#155d49', width=3)
            def enqueue(name):
                return lambda icon, item: self.events.put(name)
            self.tray = pystray.Icon('PrintBridge', image, 'PrintBridge', pystray.Menu(
                pystray.MenuItem('打开配置窗口', enqueue('show'), default=True),
                pystray.MenuItem('打开打印页', enqueue('web')),
                pystray.MenuItem('设备令牌管理', enqueue('admin')),
                pystray.MenuItem('退出', enqueue('quit'))))
            def setup(icon):
                try:
                    icon.visible = True
                    self.events.put('tray-ready')
                except Exception:
                    self.events.put('tray-error')
            def run():
                try:
                    self.tray.run(setup=setup)
                finally:
                    self.events.put('tray-error')
            threading.Thread(target=run, name='PrintBridge-tray', daemon=True).start()
        except Exception:
            self.status.set('托盘组件不可用，窗口将保持可见。')

    def poll(self):
        while True:
            try:
                event = self.events.get_nowait()
            except Empty:
                break
            if event == 'tray-ready':
                self.tray_ready = True
            elif event == 'tray-error':
                self.tray_ready = False
                self.show()
            elif event == 'show':
                self.show()
            elif event == 'quit':
                self.quit()
            elif event in ('web', 'admin'):
                self.open_page('/admin' if event == 'admin' else '/')
        if self.phase == 'starting' and self.service.ready:
            self.phase = 'running'
            self.stop_button.configure(state='normal')
            self.web_button.configure(state='normal')
            self.admin_button.configure(state='normal')
            self.status.set('模拟模式运行中 · 不会出纸' if self.running.mode == 'simulate' else '真实打印服务运行中')
            urls = [f'本机：http://127.0.0.1:{self.running.port}']
            if self.running.lan:
                urls += ['Mac 访问（请选择同一局域网地址）：'] + (lan_addresses(self.running.port) or ['未检测到地址，请在 Windows 网络设置中查看 IPv4 地址'])
            else:
                urls += ['仅本机可访问；需要 Mac 连接时请启用局域网访问。']
            self.addresses.configure(state='normal'); self.addresses.delete('1.0', 'end')
            self.addresses.insert('1.0', '\n'.join(urls)); self.addresses.configure(state='disabled')
        if self.background and self.service.ready and self.tray_ready:
            self.background = False
            self.hide()
        if self.phase != 'stopped' and not self.service.alive:
            self.phase = 'stopped'
            self.lock_form(False)
            for widget in (self.stop_button, self.web_button, self.admin_button):
                widget.configure(state='disabled')
            self.status.set(self.service.error or '服务已停止，可以修改配置。')
            if self.service.error:
                self.show()
        if self.quitting and not self.service.alive:
            if self.tray:
                self.tray.stop()
            self.root.destroy()
            return
        self.root.after(200, self.poll)


def main():
    if sys.platform != 'win32':
        raise SystemExit('桌面应用仅支持 Windows；本机开发可运行 python -m printbridge')
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    logging.getLogger().addHandler(logging.NullHandler())
    try:
        directory = user_directory()
        directory.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError):
        messagebox.showerror('PrintBridge', '无法打开用户数据目录，请检查 Windows 用户目录权限。', parent=root)
        root.destroy()
        return
    lock = FileLock(str(directory / 'desktop.lock'))
    app = None
    try:
        lock.acquire(timeout=0)
    except Timeout:
        messagebox.showinfo('PrintBridge', '程序已在运行，请从系统托盘打开。', parent=root)
        root.destroy()
        return
    try:
        try:
            config = load_config(directory)
        except ValueError as exc:
            messagebox.showerror('PrintBridge', str(exc), parent=root)
            config = None
        app = Desktop(root, directory, config, '--background' in sys.argv)
        root.deiconify()
        root.mainloop()
    finally:
        if app:
            app.service.stop()
            if app.service.thread:
                app.service.thread.join()
            if app.tray:
                app.tray.stop()
        lock.release()


if __name__ == '__main__':
    main()
