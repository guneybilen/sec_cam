import importlib
from unittest.mock import MagicMock
import pytest

# Test motion detected when numpy.sum(thresh) > threshold
def test_process_frame_pair_motion_detected(mocker):
    # Import module under test
    detection = importlib.import_module("detection")

    # Mock cv2 functions used inside _process_frame_pair
    mock_cv2 = mocker.patch("detection.cv2", autospec=True)
    # Make absdiff, cvtColor, GaussianBlur return a placeholder object
    fake = object()
    mock_cv2.absdiff.return_value = fake
    mock_cv2.cvtColor.return_value = fake
    mock_cv2.GaussianBlur.return_value = fake
    # threshold must return a tuple (retval, thresh). Provide a fake thresh object
    fake_thresh = MagicMock()
    mock_cv2.threshold.return_value = (None, fake_thresh)

    # Mock numpy.sum to be above threshold
    mocker.patch("detection.np.sum", return_value=300000)

    # Call function
    motion = detection._process_frame_pair(MagicMock(), MagicMock())
    assert motion is True

def test_process_frame_pair_no_motion(mocker):
    detection = importlib.import_module("detection")

    mock_cv2 = mocker.patch("detection.cv2", autospec=True)
    fake = object()
    mock_cv2.absdiff.return_value = fake
    mock_cv2.cvtColor.return_value = fake
    mock_cv2.GaussianBlur.return_value = fake
    fake_thresh = MagicMock()
    mock_cv2.threshold.return_value = (None, fake_thresh)

    # numpy.sum below threshold
    mocker.patch("detection.np.sum", return_value=150000)

    motion = detection._process_frame_pair(MagicMock(), MagicMock())
    assert motion is False

def test_launch_detection_starts_thread(mocker):
    # Import module under test
    detection = importlib.import_module("detection")

    # Ensure any existing thread state is cleared
    detection.detection_thread = None
    detection.detection_active_event.clear()

    # Patch threading.Thread in the detection module to capture construction
    MockThread = MagicMock()
    mocker.patch("detection.threading.Thread", MockThread)

    detection.launch_detection()

    # Event should be set
    assert detection.detection_active_event.is_set() is True

    # Thread was constructed with target=detection.main
    MockThread.assert_called_once()
    # Inspect call args to ensure target is detection.main and name/daemon are set
    called_kwargs = MockThread.call_args[1]  # kwargs from constructor
    assert called_kwargs.get("target") == detection.main
    assert called_kwargs.get("name") == "DetectionThread"
    assert called_kwargs.get("daemon") is True

def test_shutdown_detection_pipeline_clears_event_and_joins(mocker):
    detection = importlib.import_module("detection")

    # Prepare a fake running thread object with join() method
    fake_thread = MagicMock()
    fake_thread.is_alive.return_value = True
    fake_thread.join = MagicMock()
    detection.detection_thread = fake_thread

    # Ensure event is set
    detection.detection_active_event.set()

    detection.shutdown_detection_pipeline(remote=False)

    # After shutdown, event should be cleared and detection_thread set to None
    assert detection.detection_active_event.is_set() is False
    assert detection.detection_thread is None
    # join should have been called
    fake_thread.join.assert_called()

