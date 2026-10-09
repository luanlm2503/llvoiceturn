import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from llvoice import APP_NAME, __version__, i18n
from llvoice.config import DEFAULT_LANGUAGE
from llvoice.voxcpm_api import VoxCPMClient
from llvoice.ui.main_window import MainWindow

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
APP_USER_MODEL_ID = "LLVoiceTool.LLVoiceTool"


def app_icon() -> QIcon:
    icon = QIcon(str(ASSETS_DIR / "icon.ico"))
    return icon if not icon.isNull() else QIcon(str(ASSETS_DIR / "icon.png"))


def _use_own_taskbar_icon() -> None:
    # Không có ID riêng thì Windows gom app vào nhóm python.exe và hiện icon Python trên taskbar.
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)


def main() -> int:
    i18n.load(DEFAULT_LANGUAGE)

    _use_own_taskbar_icon()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    app.setApplicationVersion(__version__)

    client = VoxCPMClient()
    window = MainWindow(voxcpm_client=client)
    window.show()
    code = app.exec()
    client.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
