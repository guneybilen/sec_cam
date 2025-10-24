import threading
from unittest.mock import MagicMock
import pytest

def test_prune_active_timers():
    import utils
    # Ensure we operate on the module list object
    utils.active_timers.clear()

    # Mock threads: 2 alive, 1 finished
    thread_alive1 = MagicMock(spec=threading.Thread)
    thread_alive1.is_alive.return_value = True
    thread_alive2 = MagicMock(spec=threading.Thread)
    thread_alive2.is_alive.return_value = True
    thread_dead = MagicMock(spec=threading.Thread)
    thread_dead.is_alive.return_value = False

    utils.active_timers.extend([thread_alive1, thread_alive2, thread_dead])

    # Call the function
    utils.prune_active_timers()

    # After calling, utils.active_timers should have been rebound to the filtered list
    assert len(utils.active_timers) == 2
    assert all(t.is_alive() for t in utils.active_timers)

# compress_video success scenario using monkeypatch/mocker
def test_compress_video_success(monkeypatch, tmp_path, mocker):
    import utils, subprocess, os
    # Create a fake input file
    input_file = tmp_path / "video.avi"
    input_file.write_bytes(b"FAKE")  # content doesn't matter for test

    # Patch os.path.exists to return True for the input and for output when appropriate
    monkeypatch.setattr(utils.os.path, "exists", lambda p: True)

    # Patch subprocess.check_output to simulate ffprobe returning duration "10.0\n"
    mock_check_output = mocker.patch.object(utils.subprocess, "check_output", return_value=b"10.0\n")
    # Patch subprocess.run to simulate successful ffmpeg run
    mock_run = mocker.patch.object(utils.subprocess, "run", return_value=None)
    # Patch get_ffmpeg_exe to return a dummy ffmpeg path
    monkeypatch.setattr(utils, "get_ffmpeg_exe", lambda: "/usr/bin/ffmpeg")

    # Patch os.remove to observe removal of original file
    mock_remove = mocker.patch.object(utils.os, "remove", autospec=True)

    # Call compress_video
    out = utils.compress_video(str(input_file), target_size_mb=1)

    # Assertions
    # Should have attempted to call ffprobe and ffmpeg
    mock_check_output.assert_called()
    mock_run.assert_called()
    # compress_video returns an output path (endswith .mp4)
    assert out is not None and out.endswith(".mp4")

