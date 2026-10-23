import subprocess


def command(settings, job, path):
    if job["sides"] not in {"simplex", "duplexlong", "duplexshort"} or not 1 <= job["copies"] <= 99:
        raise ValueError("无效打印参数")
    return [settings.sumatra, "-new-window", "-print-to", settings.printer,
            "-print-settings", f'paper=A4,fit,monochrome,{job["sides"]},{job["copies"]}x',
            "-silent", str(path.resolve())]


def print_pdf(settings, job, path):
    if settings.mode == "simulate":
        return "simulated", "模拟处理完成，未发送到打印机。"
    try:
        result = subprocess.run(command(settings, job, path), timeout=settings.timeout,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        return "uncertain", "打印程序超时，可能已提交；请核查 Windows 队列和出纸情况。"
    except OSError:
        return "failed", "无法启动打印程序，请检查服务器配置。"
    if result.returncode != 0:
        return "uncertain", "打印程序返回错误，可能已部分打印；请核查后再决定是否重新提交。"
    return "submitted", "打印程序已返回成功；尚未确认实际出纸，请检查打印机。"
