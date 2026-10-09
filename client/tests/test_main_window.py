from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from llvoice import i18n
from llvoice.ui.main_window import MainWindow
from llvoice.ui.tts_page import TtsPage


class FakeLocalClient:
    def health(self):
        return {"status": "ready"}

def test_main_window_shows_single_voxcpm_screen():
    i18n.load("vi")
    app = QApplication.instance() or QApplication([])
    pool = QThreadPool()
    window = MainWindow(voxcpm_client=FakeLocalClient(), thread_pool=pool)
    assert isinstance(window.centralWidget(), TtsPage)
    assert window.centralWidget() is window.tts_page
    assert pool.waitForDone(1000)
    app.processEvents()
    window.close()
