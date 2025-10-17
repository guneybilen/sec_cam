# tests/test_utils.py
import os
import time
import asyncio
import tkinter as tk
from motion_detector import compress_video
from motion_detector import update_cooldown_label
from motion_detector import clean_old_clips
from motion_detector import _set_cooldown_label_text
from motion_detector import _toggle_autostart_label_color
from motion_detector import update_gui_idle_state
from motion_detector import build_gui_commands
from motion_detector import build_gui_widgets
from motion_detector import shutdown_telegram_listener

def test_compress_video_invalid_path():
    result = compress_video("nonexistent.avi")
    assert result is None, "Should return None for invalid input path"
# tests/test_cleanup.py

def test_clean_old_clips_removes_old_files(tmp_path):
    # Create dummy clip folder
    clip_dir = tmp_path / "clips"
    clip_dir.mkdir()

    # Create an old file
    old_file = clip_dir / "old_clip.avi"
    old_file.write_text("dummy")
    old_time = time.time() - (8 * 86400)  # 8 days ago
    os.utime(old_file, (old_time, old_time))

    # Run cleanup
    clean_old_clips(str(clip_dir), days=7)

    # Check file is deleted
    assert not old_file.exists(), "Old clip should be deleted"

#def _set_cooldown_label_text(seconds_left):
#    if cooldown_label:
#        cooldown_label.config(text=f"Cooldown: {seconds_left}s")


class DummyLabel:
    def __init__(self):
        self.text = ""
        self.fg = ""
    def config(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]
        if "fg" in kwargs:
            self.fg = kwargs["fg"]

class DummyButton:
    def __init__(self):
        self.state = ""
    def config(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]


def update_cooldown_label(seconds_left):
    if not detection_active or root is None or not root.winfo_exists():
        return
    root.after(0, lambda s=seconds_left: _set_cooldown_label_text(s))

def test_set_cooldown_label_text_direct():
    dummy_label = DummyLabel()
    _set_cooldown_label_text(dummy_label, 10)
    assert dummy_label.text == "Cooldown: 10s"

class DummyLabel2:
    def __init__(self):
        self.bg = "green"
    def cget(self, attr):
        if attr == "bg":
            return self.bg
    def config(self, **kwargs):
        if "bg" in kwargs:
            self.bg = kwargs["bg"]

class DummyRoot:
    def winfo_exists(self):
        return True

def test_toggle_autostart_label_color():
    label = DummyLabel2()
    _toggle_autostart_label_color(label)
    assert label.bg == "limegreen"

    _toggle_autostart_label_color(label)
    assert label.bg == "green"

def test_update_gui_idle_state(monkeypatch):
    dummy_label = DummyLabel()
    dummy_start = DummyButton()
    dummy_stop = DummyButton()

    monkeypatch.setattr("motion_detector.cooldown_label", dummy_label)
    monkeypatch.setattr("motion_detector.start_button", dummy_start)
    monkeypatch.setattr("motion_detector.stop_button", dummy_stop)
    monkeypatch.setattr("motion_detector.root", DummyRoot())

    update_gui_idle_state()

    assert dummy_label.text == "Idle"
    assert dummy_start.state == "normal"
    assert dummy_stop.state == "disabled"


class DummyCap:
    def __init__(self):
        self.released = False
    def isOpened(self):
        return True
    def release(self):
        self.released = True

def test_release_camera_resource(monkeypatch):
    dummy_cap = DummyCap()
    monkeypatch.setattr("motion_detector.cap", dummy_cap)

    from motion_detector import release_camera_resource
    release_camera_resource()

    assert dummy_cap.released is True

class DummyTelegramApp:
    async def stop(self):
        print("[Mock] Telegram app stopped.")

class DummyLoop:
    def is_running(self):
        return True
    def call_soon_threadsafe(self, callback):
        callback()

class DummyThread:
    def __init__(self):
        self.alive = True
    def is_alive(self):
        return self.alive
    def join(self, timeout=None):
        self.alive = False

def test_shutdown_telegram_listener(monkeypatch):
    # Create a real asyncio loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Set up dummy Telegram app and thread
    monkeypatch.setattr("motion_detector.telegram_app", DummyTelegramApp())
    monkeypatch.setattr("motion_detector.telegram_loop", loop)
    monkeypatch.setattr("motion_detector.telegram_thread", DummyThread())

    # Run the shutdown logic
    shutdown_telegram_listener()

    # Let the loop process scheduled tasks
    loop.run_until_complete(asyncio.sleep(0.1))

    # Clean up
    loop.close()


def test_build_gui_widgets_real_root():
    root = tk.Tk()
    commands = build_gui_commands()
    widgets = build_gui_widgets(root, commands)

    assert "start" in widgets
    assert widgets["start"].cget("text") == "Start Detection"

    root.destroy()  # Clean up the GUI window
