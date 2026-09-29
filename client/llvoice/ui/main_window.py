from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QLabel, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from llvoice.api import ApiClient, ApiError
from llvoice.i18n import tr

TABS = ["tab.tts", "tab.srt", "tab.voices", "tab.queue", "tab.history", "tab.account"]


class _HealthSignals(QObject):
    done = Signal(object)


class _HealthCheck(QRunnable):
    """Gọi /health ở luồng nền để không đơ giao diện khi mạng chậm."""

    def __init__(self, api: ApiClient) -> None:
        super().__init__()
        self.api = api
        self.signals = _HealthSignals()

    def run(self) -> None:
        try:
            self.signals.done.emit(self.api.health())
        except ApiError as exc:
            self.signals.done.emit(exc)


def _placeholder() -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    label = QLabel(tr("placeholder.coming_soon"))
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(label)
    return page


class MainWindow(QMainWindow):
    def __init__(self, api: ApiClient) -> None:
        super().__init__()
        self.api = api
        self.setWindowTitle(tr("app.title"))
        self.resize(1100, 720)

        tabs = QTabWidget()
        for key in TABS:
            tabs.addTab(_placeholder(), tr(key))
        self.setCentralWidget(tabs)

        self.statusBar().showMessage(tr("status.connecting"))
        self._check_server()

    def _check_server(self) -> None:
        job = _HealthCheck(self.api)
        job.signals.done.connect(self._on_health)
        QThreadPool.globalInstance().start(job)

    def _on_health(self, result: object) -> None:
        if isinstance(result, ApiError):
            self.statusBar().showMessage(tr("status.offline"))
        else:
            self.statusBar().showMessage(tr("status.online", version=result["version"]))
