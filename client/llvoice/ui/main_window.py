from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QMainWindow

from llvoice.i18n import tr
from llvoice.ui.tts_page import TtsPage


class MainWindow(QMainWindow):
    def __init__(self, *, voxcpm_client=None, thread_pool: QThreadPool | None = None) -> None:
        super().__init__()
        self.setWindowTitle(tr("app.title"))
        self.resize(1000, 640)
        self.tts_page = TtsPage(voxcpm_client, thread_pool=thread_pool)
        self.setCentralWidget(self.tts_page)

    def closeEvent(self, event):
        self.tts_page.close()
        super().closeEvent(event)
