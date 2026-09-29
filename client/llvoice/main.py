import sys

from PySide6.QtWidgets import QApplication

from llvoice import APP_NAME, __version__, i18n
from llvoice.api import ApiClient
from llvoice.config import DEFAULT_LANGUAGE
from llvoice.ui.main_window import MainWindow


def main() -> int:
    i18n.load(DEFAULT_LANGUAGE)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)

    api = ApiClient()
    window = MainWindow(api)
    window.show()
    code = app.exec()
    api.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
