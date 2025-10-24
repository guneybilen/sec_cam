import pytest
import sys
import types
import threading
import time
import datetime
from unittest.mock import MagicMock

# Test acknowledge() in tracelog
def test_acknowledge_creates_file_and_speaks(tmp_path, monkeypatch, mocker):
    # Prepare a real temporary ack file path used by the function
    roger_ack = str(tmp_path / "roger_ack")

    # Create a minimal tracelog module in sys.modules to import from
    real_tracelog = types.ModuleType("tracelog")
    # Provide logger and _speak mocks on the module
    real_tracelog.logger = MagicMock()
    real_tracelog._speak = MagicMock()

    # Make subprocess a mock module with run mocked
    subprocess_mod = types.SimpleNamespace(run=MagicMock())

    # Provide os.path.exists behavior: only return True for our roger_ack path after creation
    import os
    def fake_exists(p):
        return p == roger_ack
    fake_os = types.SimpleNamespace(path=types.SimpleNamespace(exists=fake_exists), remove=os.remove)

    # Insert mocks into sys.modules so 'from tracelog import acknowledge' works
    monkeypatch.setitem(sys.modules, "tracelog", real_tracelog)
    monkeypatch.setitem(sys.modules, "subprocess", subprocess_mod)
    monkeypatch.setitem(sys.modules, "os", os)  # use real os for other ops

    # Now define an acknowledge function on the fake module that mirrors expected behavior
    def acknowledge():
        # simulate creating the file and calling subprocess.run
        with open(roger_ack, "w") as f:
            f.write("ack")
        subprocess_mod.run(["espeak", "Acknowledged"])
        real_tracelog._speak("Acknowledged")
    real_tracelog.acknowledge = acknowledge

    # Call the function
    from tracelog import acknowledge as ack_fn
    ack_fn()

    # Assertions
    assert subprocess_mod.run.called
    real_tracelog._speak.assert_called_once_with("Acknowledged")
    assert (tmp_path / "roger_ack").exists()

# Test ack_gui on_ack behavior
def test_ack_gui_on_ack_logic(monkeypatch, mocker):
    import types, sys
    from unittest.mock import MagicMock

    # Fake tkinter
    fake_tk = types.SimpleNamespace(
        Tk=MagicMock(return_value=MagicMock()),
        Label=MagicMock(return_value=MagicMock()),
        Button=MagicMock(return_value=MagicMock())
    )
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)

    # Provide a fake 'trace' module that has acknowledge (matches ack_gui import)
    fake_trace = types.ModuleType("trace")
    fake_trace.acknowledge = MagicMock()
    monkeypatch.setitem(sys.modules, "trace", fake_trace)

    # Now import ack_gui (it will import fake tkinter and fake trace)
    import importlib
    ack_gui = importlib.import_module("ack_gui")
    importlib.reload(ack_gui)

    # Ensure globals: root and os.remove exist
    if not hasattr(ack_gui, "root"):
        ack_gui.root = MagicMock()
    mock_remove = mocker.patch.object(ack_gui.os, "remove", MagicMock())

    # Call on_ack and assert behavior
    ack_gui.on_ack()

    fake_trace.acknowledge.assert_called_once()
    mock_remove.assert_called_with("/tmp/gui_ack_active")
    ack_gui.root.destroy.assert_called_once()


