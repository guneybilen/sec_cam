import sys
import types
import importlib
import pytest
from unittest.mock import MagicMock

# Minimal fake signal used by GuiDispatcher
class _FakeSignal:
    def __init__(self, *args, **kwargs):
        self._slot = None
    def connect(self, slot):
        self._slot = slot
    def emit(self, *args, **kwargs):
        if self._slot:
            try:
                self._slot(*args, **kwargs)
            except TypeError:
                self._slot()

@pytest.fixture(autouse=True)
def inject_minimal_gui_env(monkeypatch):
    """Inject minimal replacements so importing gui.py doesn't import real Qt/qasync."""
    # qasync no-op decorators
    def _noop_decorator(f):
        return f
    fake_qasync = types.SimpleNamespace(asyncSlot=_noop_decorator, asyncClose=_noop_decorator)
    monkeypatch.setitem(sys.modules, "qasync", fake_qasync)

    # Minimal PyQt5 pieces used by gui.py
    fake_qtwidgets = types.SimpleNamespace(
        QApplication=MagicMock,
        QMainWindow=MagicMock,
        QWidget=MagicMock,
        QPushButton=MagicMock,
        QLabel=MagicMock,
        QVBoxLayout=MagicMock,
        QMessageBox=MagicMock,  # will be overridden per-test when needed
        QTextEdit=MagicMock,
        QSizePolicy=MagicMock,
    )
    fake_qtcore = types.SimpleNamespace(
        QTimer=MagicMock,
        Qt=MagicMock,
        QObject=type("Obj", (), {}),
        # pyqtSignal factory -> return a fake signal object
        pyqtSignal=lambda *a, **k: _FakeSignal(),
        QCoreApplication=type("QCoreApp", (), {"instance": staticmethod(lambda: None)})
    )
    fake_qtgui = types.SimpleNamespace(QImage=MagicMock, QPixmap=MagicMock, QCloseEvent=MagicMock)

    monkeypatch.setitem(sys.modules, "PyQt5.QtWidgets", fake_qtwidgets)
    monkeypatch.setitem(sys.modules, "PyQt5.QtCore", fake_qtcore)
    monkeypatch.setitem(sys.modules, "PyQt5.QtGui", fake_qtgui)

    # Lightweight replacements for other heavy deps
    monkeypatch.setitem(sys.modules, "cv2", MagicMock())
    monkeypatch.setitem(sys.modules, "tracelog", MagicMock())
    monkeypatch.setitem(sys.modules, "subprocess", MagicMock())
    monkeypatch.setitem(sys.modules, "platform", MagicMock())
    monkeypatch.setitem(sys.modules, "logging", MagicMock())

    yield

def test_enqueue_gui_calls_dispatcher(monkeypatch):
    # Import gui after environment is prepared
    gui = importlib.reload(importlib.import_module("gui"))

    called = {}
    def sample_func():
        called['ok'] = True

    # Ensure _dispatcher exists
    if not hasattr(gui, "_dispatcher"):
        pytest.skip("gui._dispatcher not present")

    # Override the dispatcher's signal emit to call the passed callable synchronously
    sig = gui._dispatcher.update_signal
    monkeypatch.setattr(sig, "emit", lambda fn=None: fn() if callable(fn) else None)

    gui.enqueue_gui(sample_func)
    assert called.get("ok") is True

def test_clear_logs_success(monkeypatch):
    import builtins
    # Prepare fake QMessageBox in PyQt5.QtWidgets before importing gui
    class FakeQMessageBox:
        Yes = 1
        No = 0
        @staticmethod
        def question(*args, **kwargs):
            return FakeQMessageBox.Yes
        @staticmethod
        def information(*args, **kwargs):
            return None

    qtwidgets = sys.modules.get("PyQt5.QtWidgets")
    setattr(qtwidgets, "QMessageBox", FakeQMessageBox)

    gui = importlib.reload(importlib.import_module("gui"))

    opened = {}
    def fake_open(path, mode='r', *args, **kwargs):
        opened['path'] = path
        opened['mode'] = mode
        class DummyFile:
            def write(self, data): opened['written'] = data
            def __enter__(self): return self
            def __exit__(self, exc_type, exc, tb): return False
        return DummyFile()

    monkeypatch.setattr(builtins, "open", fake_open)

    cmds = gui.build_gui_commands()
    cmds["clear_logs"]()

    assert opened.get('mode') == 'w'
    assert opened.get('written') == ""



def test_send_summary_now(monkeypatch, mocker):
    # Prepare fake QMessageBox (information only)
    class FakeQMessageBox:
        @staticmethod
        def information(*args, **kwargs):
            return None
        @staticmethod
        def question(*args, **kwargs):
            return None

    qtwidgets = sys.modules.get("PyQt5.QtWidgets")
    setattr(qtwidgets, "QMessageBox", FakeQMessageBox)

    gui = importlib.reload(importlib.import_module("gui"))

    mock_send = mocker.patch("utils.send_daily_summary")
    cmds = gui.build_gui_commands()
    cmds["send_summary_now"]()
    mock_send.assert_called_once()

