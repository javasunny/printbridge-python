import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    runtime: Path = Path("runtime")
    mode: str = "simulate"
    printer: str = ""
    sumatra: str = ""
    max_upload_mb: int = 25
    timeout: int = 120
    retention_hours: int = 24
    ipp_enabled: bool = False

    def validate(self):
        if self.mode not in {"simulate", "windows"}:
            raise ValueError("PRINT_MODE 必须为 simulate 或 windows")
        if min(self.max_upload_mb, self.timeout, self.retention_hours) < 1:
            raise ValueError("大小、超时和保留时间必须大于零")
        if self.mode == "windows":
            if os.name != "nt":
                raise ValueError("PRINT_MODE=windows 仅能在 Windows 上运行；本机测试请使用 simulate")
            if not self.printer.strip():
                raise ValueError("PRINT_PRINTER 为空，请填写 Windows 中的完整打印机名称")
            if not self.sumatra.strip():
                raise ValueError("PRINT_SUMATRA 为空，请填写 SumatraPDF.exe 的完整路径")
            if not Path(self.sumatra).is_file():
                raise ValueError("PRINT_SUMATRA 指向的文件不存在，请填写实际的 SumatraPDF.exe 文件路径，而非目录；路径值内不要额外嵌入引号")

    @classmethod
    def from_env(cls):
        return cls(
            runtime=Path(os.getenv("PRINT_RUNTIME", "runtime")).resolve(),
            mode=os.getenv("PRINT_MODE", "simulate"),
            printer=os.getenv("PRINT_PRINTER", ""),
            sumatra=os.getenv("PRINT_SUMATRA", ""),
            max_upload_mb=int(os.getenv("PRINT_MAX_UPLOAD_MB", "25")),
            timeout=int(os.getenv("PRINT_TIMEOUT_SECONDS", "120")),
            retention_hours=int(os.getenv("PRINT_RETENTION_HOURS", "24")),
            ipp_enabled=os.getenv("PRINT_IPP_ENABLED", "0") == "1",
        )
