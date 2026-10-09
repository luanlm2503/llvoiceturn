from llvoice import main as main_module


def test_main_constructs_only_voxcpm_client(monkeypatch):
    created = []
    class FakeApp:
        def __init__(self, args): pass
        def setApplicationName(self, name): pass
        def setApplicationVersion(self, version): pass
        def setWindowIcon(self, icon): created.append(("icon", icon.isNull()))
        def exec(self): return 0
    class FakeClient:
        def close(self): created.append("closed")
    class FakeWindow:
        def __init__(self, *, voxcpm_client): created.append(voxcpm_client)
        def show(self): pass
    monkeypatch.setattr(main_module, "QApplication", FakeApp)
    monkeypatch.setattr(main_module, "VoxCPMClient", FakeClient)
    monkeypatch.setattr(main_module, "MainWindow", FakeWindow)
    # QIcon cần QApplication thật; FakeApp không tạo nên thay bằng icon giả.
    monkeypatch.setattr(main_module, "app_icon", lambda: type("Icon", (), {"isNull": lambda self: False})())
    monkeypatch.setattr(main_module, "_use_own_taskbar_icon", lambda: created.append("taskbar"))
    main_module.main()
    assert created.pop(0) == "taskbar"
    assert created[0] == ("icon", False)
    assert isinstance(created[1], FakeClient)
    assert created[2] == "closed"


def test_app_icon_files_load():
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    icon = main_module.app_icon()
    assert not icon.isNull()
    assert max(size.width() for size in icon.availableSizes()) == 256
    image = QImage(str(main_module.ASSETS_DIR / "icon.png"))
    assert image.width() == 256 and image.hasAlphaChannel()
