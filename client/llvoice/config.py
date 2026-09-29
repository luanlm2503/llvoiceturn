import os
from pathlib import Path

from llvoice import APP_NAME

# Địa chỉ proxy. build.ps1 sinh file _build.py để ghi cứng vào exe;
# khi chạy dev không có file đó thì đọc biến môi trường.
try:
    from llvoice._build import API_BASE_URL
except ImportError:
    API_BASE_URL = os.environ.get("LLV_API_BASE_URL", "http://127.0.0.1:8000")

DEFAULT_LANGUAGE = "vi"


def data_dir() -> Path:
    """Thư mục lưu SQLite, token, file đã tạo: %LOCALAPPDATA%\\LLVoiceTool."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path
