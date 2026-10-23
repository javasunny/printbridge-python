from types import SimpleNamespace
from unittest.mock import patch

import pytest

from printbridge.config import Settings


def test_windows_config_identifies_missing_setting(tmp_path):
    with patch("printbridge.config.os", SimpleNamespace(name="nt")):
        with pytest.raises(ValueError, match="PRINT_PRINTER 为空"):
            Settings(mode="windows", printer=" ").validate()
        with pytest.raises(ValueError, match="PRINT_SUMATRA 为空"):
            Settings(mode="windows", printer="TOSHIBA").validate()
        with pytest.raises(ValueError, match="文件不存在"):
            Settings(mode="windows", printer="TOSHIBA", sumatra=str(tmp_path)).validate()
        executable = tmp_path / "SumatraPDF.exe"
        executable.touch()
        Settings(mode="windows", printer="TOSHIBA", sumatra=str(executable)).validate()
