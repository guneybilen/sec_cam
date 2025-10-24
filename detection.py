# detection.py
import cv2
import time
import os
import tracelog as T
import threading
import numpy as np
from datetime import datetime
from contextlib import contextmanager # Import contextmanager
import asyncio
import subprocess

# from PyQt5.QtCore import Qt
# from PyQt5.QtGui import QImage, QPixmap
# from utils import open_video_writer, compress_video, gui_after
# from gui import safe_imshow, update_cooldown_label, update_gui_idle_state
# from notifications import send_alerts_async, send_telegram_error_alert

from PyQt5.QtWidgets import QMessageBox, QApplication
from PyQt5.QtCore import Qt

# Global Variables
cap = None  # Camera capture object
detection_active_event = threading.Event()  # Flag to control detection loop
detection_thread = None
recording_in_progress = False
last_motion_time = 0
manual_shutdown_requested = False   # ✅ new flag
# GUI dispatch health and throttle
_preview_enabled = True
_preview_last_ts = 0.0
_preview_min_interval = 0.08  # ~12.5 FPS equivalent; avoid spamming GUI
_sudo_shutdown_lock = threading.Lock()
_sudo_shutdown_flag = False

def set_sudo_shutdown_in_progress(value: bool):
    global _sudo_shutdown_flag
    with _sudo_shutdown_lock:
        _sudo_shutdown_flag = value

def get_sudo_shutdown_in_progress() -> bool:
    with _sudo_shutdown_lock:
        return _sudo_shutdown_flag


def _gui_post(func, *args, **kwargs):
    """
    Safely enqueue a GUI function onto the Qt main thread.
    Falls back silently if GUI is not active or dispatcher is unavailable.
    """
    try:
        from gui import gui_exists, enqueue_gui
        if gui_exists():
            enqueue_gui(func, *args, **kwargs)
        else:
            T.info("GUI not active; skipping GUI post.")
    except Exception as e:
        # If the dispatcher itself touches asyncio in a worker thread,
        # avoid repeated warnings flooding the logs.
        T.warning(f"GUI post failed: {e}")
        raise


def _process_frame_pair(frame1, frame2):
    """
    Compares two frames to detect motion.

    Args:
        frame1: The first frame (numpy array).
        frame2: The second frame (numpy array).

    Returns:
        True if motion is detected, False otherwise.
    """
    diff = cv2.absdiff(frame1, frame2)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blur, 20, 255, cv2.THRESH_BINARY)
    #motion_score = np.sum(thresh)
    #print(f"Motion score: {motion_score}")
    return np.sum(thresh) > 200000


def save_clip(cap_instance, duration=5, fps=20):
    """Records a video clip from the camera (no direct GUI calls here)."""
    os.makedirs("clips", exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    avi_path = f"clips/motion_{timestamp}.avi"

    if not cap_instance or not cap_instance.isOpened():
        T.error("[!] Failed to access webcam during clip save.")
        return None

    width = int(cap_instance.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap_instance.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fourcc = cv2.VideoWriter_fourcc(*"DIVX")

    frames_recorded = 0
    start_time = time.time()
    from utils import open_video_writer
    with open_video_writer(avi_path, fourcc, fps, (width, height)) as out:
        while time.time() - start_time < duration:
            if not detection_active_event.is_set():
                break

            ret, frame = cap_instance.read()
            if not ret:
                break

            out.write(frame)
            frames_recorded += 1


            # Thread-safe GUI preview with throttle and kill-switch
            try:
                global _preview_enabled, _preview_last_ts, _preview_min_interval
                if _preview_enabled:
                    now_ts = time.time()
                    if now_ts - _preview_last_ts >= _preview_min_interval:
                        _preview_last_ts = now_ts
                        from gui import gui_active, safe_imshow_threadsafe
                        if gui_active:
                            # Prefer args form to avoid capturing large frames in lambda
                            _gui_post(safe_imshow_threadsafe, frame)
            except Exception as e:
                T.warning(f"Preview frame dispatch failed: {e}")
                # Kill-switch: if dispatcher lacks an event loop in worker thread, stop preview attempts
                if "no current event loop" in str(e).lower():
                    _preview_enabled = False
                    T.warning("Preview disabled due to missing event loop in worker thread.")

    if frames_recorded > 0:
        T.info(f"[✔] Saved motion clip with {frames_recorded} frames to {avi_path}")
        return avi_path
    else:
        if os.path.exists(avi_path):
            os.remove(avi_path)
        T.error("[!] Clip save failed (0 frames recorded).")
        return None


def _handle_motion_event(cap, cooldown):
    """Handles motion detection event safely with debug output."""
    global last_motion_time, recording_in_progress

    from notifications import send_alerts_async, increment_motion_count

    T.info("[DEBUG] Handling motion event start")
    try:
        # Mark state
        last_motion_time = datetime.now()
        recording_in_progress = True
        increment_motion_count()

        avi_file = save_clip(cap)
        if not avi_file:
            T.error("[!] save_clip returned None — aborting motion event")
            recording_in_progress = False
            return

        T.info(f"[DEBUG] Saved clip: {avi_file}")

        from utils import compress_video
        mp4_file = compress_video(avi_file)
        T.info(f"[DEBUG] Compressed to: {mp4_file}")

        send_alerts_async(mp4_file)
        T.info("[DEBUG] Alerts dispatched")

        _run_cooldown(cooldown)
        T.info("[DEBUG] Cooldown completed")

    except Exception as e:
        # print(f"[❌] Exception in _handle_motion_event: {e}")
        T.error(f"Motion event failed: {e}")
    finally:
        # Always reset flag at the end
        recording_in_progress = False

def _run_cooldown(seconds):
    """
    Cooldown after a motion event. No direct Qt calls in this thread.
    """
    for i in range(seconds, 0, -1):
        if not detection_active_event.is_set():
            break

        from gui import enqueue_gui, update_cooldown_label
        enqueue_gui(update_cooldown_label, i)
        time.sleep(1)

    # After cooldown, update GUI state
    try:
        from gui import gui_exists, enqueue_gui, set_cooldown_detecting_threadsafe
        if detection_active_event.is_set() and gui_exists():
            enqueue_gui(set_cooldown_detecting_threadsafe)
    except Exception as e:
        T.warning(f"Cooldown end dispatch failed: {e}")


def release_camera_resource():
    """Safely releases the camera if it's open."""
    global cap
    if cap is not None and hasattr(cap, "isOpened") and cap.isOpened():
        cap.release()
        cap = None
        T.info("[📷] Camera resource released.")
        time.sleep(1.0)  # Give time for driver to settle


def _detection_loop(cam):
    """The main motion detection loop."""
    global cap, last_motion_time, recording_in_progress
    cap = cam
    last_alert_time = 0
    cooldown = 30

    # Enqueue GUI init onto the Qt main thread
    try:
        from gui import init_widgets_for_boot
        _gui_post(init_widgets_for_boot)
    except Exception as e:
        T.warning(f"Failed to enqueue GUI boot init: {e}")

    while detection_active_event.is_set():
        if cap is None or not hasattr(cap, 'read'):
            T.error('Camera object lost or invalid. Exiting detection loop.')
            break

        ret1, frame1 = cap.read()
        time.sleep(0.05)
        ret2, frame2 = cap.read()

        if not ret1 or not ret2:
            time.sleep(1)
            continue

        if _process_frame_pair(frame1, frame2):
            T.info("[DEBUG] Motion detected")
            now = time.time()

            if not recording_in_progress and (now - last_alert_time) > cooldown:
                last_motion_time = datetime.now()
                last_alert_time = now
                _handle_motion_event(cap, cooldown)
                T.info("[✔] Motion recorded. Cooldown started.")
            elif recording_in_progress:
                T.info("[⏳] Motion detected but already recording.")
            else:
                # Cooldown active
                T.info("[⏳] Motion detected but cooldown is active.")


@contextmanager
def open_camera(index=0):
    """Context manager for opening and releasing the camera."""
    global cap
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        cap = None
        raise IOError(f"Cannot open camera at index {index}")
    try:
        yield cap
    finally:
        # CRITICAL FIX: The camera resource is NOT released here.
        # It MUST be released in the main_entry's finally block OR
        # the detection thread's finally block to avoid premature release.
        pass
     
def show_spinner():
    try:
        from gui import cooldown_label as status_label
        if status_label:
            status_label.setText("🔄 Waiting for authorization...")
    except Exception as e:
        T.warning(f"Spinner failed: {e}")

def hide_spinner():
    try:
        from gui import cooldown_label as status_label
        if status_label:
            status_label.setText("")
    except Exception as e:
        T.warning(f"Spinner hide failed: {e}")

        
def disable_stop_button():
    from gui import stop_button
    if stop_button:
        stop_button.setEnabled(False)

def enable_stop_button():
    from gui import stop_button
    if stop_button:
        stop_button.setEnabled(True)
        
            
def _shutdown_after_user_confirmation():
    from gui import enqueue_gui
    enqueue_gui(disable_stop_button)
    enqueue_gui(show_spinner)
    
    global detection_thread, manual_shutdown_requested, sudo_shutdown_in_progress
    manual_shutdown_requested = True
    set_sudo_shutdown_in_progress(False)
    T.info("[🛑] Detection flag set to False.")

    if detection_thread and detection_thread.is_alive():
        detection_thread.join(timeout=5)
        if detection_thread and detection_thread.is_alive():
            T.warning("Detection thread failed to join gracefully.")
            try:
                from gui import enqueue_gui, cooldown_label, gui_after
                if cooldown_label:
                    enqueue_gui(lambda: cooldown_label.setText("⚠️ Detection thread hang"))
                    gui_after(10000, lambda: enqueue_gui(
                        lambda: cooldown_label.setText("") if cooldown_label else None
                    ))
            except Exception as e:
                T.warning(f"GUI warning dispatch failed: {e}")

    detection_thread = None

    try:
        from gui import gui_exists, enqueue_gui, update_gui_idle_state
        if gui_exists():
            enqueue_gui(update_gui_idle_state)
        else:
            T.info("GUI not active; skipping idle state update.")
    except Exception as e:
        T.warning(f"Idle state dispatch failed: {e}")

    T.info("Motion detection stopped.")
    detection_active_event.clear()

"""
def _run_sudo_shutdown_worker():
    global detection_active_event, sudo_shutdown_in_progress
    try:
        subprocess.run(["sudo", "-k"], check=True)
        subprocess.run(["xterm", "-e", "sudo ./stop_detector_secure.sh"])
        T.info("[SECURITY] Sudo shutdown succeeded.")
        detection_active_event.clear()
    except Exception as e:
        T.error(f"[SECURITY] Shutdown error: {e}\n{traceback.format_exc()}")
        detection_active_event.set()
    finally:
        set_sudo_shutdown_in_progress(False)
        enqueue_gui(hide_spinner)
        enqueue_gui(enable_stop_button)
def _run_sudo_shutdown_main_thread():
    enqueue_gui(show_spinner)
    T.info("[THREAD] Launching sudo shutdown thread")
    t = threading.Thread(target=_run_sudo_shutdown_worker, name="SudoShutdownThread")
    t.start()
"""


def show_sudo_info_popup(*args, **kwargs):
    global sudo_info_popup
    T.info("[UI] Showing sudo info popup")

    app = QApplication.instance()
    if not app:
        T.warning("No QApplication instance found.")
        return

    msg = QMessageBox()
    msg.setIcon(QMessageBox.Information)
    msg.setWindowTitle("Authorization Required")
    msg.setText("🔐 Please authorize the shutdown.\n\nA system prompt will appear asking for your password.")
    msg.setStandardButtons(QMessageBox.Ok)
    msg.setDefaultButton(QMessageBox.Ok)
    msg.setWindowModality(Qt.ApplicationModal)

    # Center the message box
    screen = app.primaryScreen()
    screen_geometry = screen.availableGeometry()
    msg_geometry = msg.frameGeometry()
    center_point = screen_geometry.center()
    msg_geometry.moveCenter(center_point)
    msg.move(msg_geometry.topLeft())

    sudo_info_popup = msg
    msg.raise_()
    msg.activateWindow()

    result = msg.exec_()
    if result != QMessageBox.Ok:
        T.info("User cancelled shutdown.")
        return

    # ✅ Run sudo in a background thread to keep GUI responsive
    def run_sudo_in_terminal():
        try:
            T.info("[SECURITY] Launching sudo in terminal")
            subprocess.run(["sudo", "-k"], check=True)
            subprocess.run(["gnome-terminal","--","bash","-c","sudo ./stop_detector_secure.sh"], check=True)
            T.info("[SECURITY] Sudo shutdown succeeded.")
            set_sudo_shutdown_in_progress(False)
            detection_active_event.clear()
        except subprocess.CalledProcessError as e:
            T.error(f"[SECURITY] Sudo authorization failed: {e}")
            detection_active_event.set()
        except Exception as e:
            T.error(f"[SECURITY] Unexpected error during sudo escalation: {e}")
            detection_active_event.set()
        finally:
            from gui import enqueue_gui
            set_sudo_shutdown_in_progress(False)
            enqueue_gui(hide_spinner)
            enqueue_gui(enable_stop_button)
            _shutdown_after_user_confirmation()

    threading.Thread(target=run_sudo_in_terminal, name="SudoTerminalThread").start()


def shutdown_detection_pipeline(skip_auth=False):
    global sudo_shutdown_in_progress
    if not skip_auth:
        if get_sudo_shutdown_in_progress():
            T.warning("Shutdown already in progress — ignoring duplicate request.")
            return False

        set_sudo_shutdown_in_progress(True)
        _gui_post(show_sudo_info_popup)  # Show popup and run sudo after confirmation
        return True
           
              
def main():
    """Main motion detection loop running in a background thread."""
    from gui import gui_exists
    global cap, detection_thread
    T.info("[DEBUG] Detection thread started.")
    T.info("[🎬] Detection thread started.")

    # Attempt to open camera using context manager
    try:
        with open_camera() as cam:
            if cam is None or not cam.isOpened():
                T.warning("open_camera() failed. Trying fallback initialization.")
                cam = cv2.VideoCapture(0)
                if not cam or not cam.isOpened():
                    T.error("Camera initialization failed completely.")
                    from gui import gui_exists, show_camera_error_threadsafe, enable_start_button_threadsafe
                    if gui_exists():
                        if gui_exists():
                            show_camera_error_threadsafe()
                            enable_start_button_threadsafe()
                    return
                T.info("Fallback camera initialization succeeded.")
            _detection_loop(cam)
            # Add the initialization of the init_widgets here

    except Exception as e:
        T.error(f"Detection thread crashed: {e}")
    finally:
        release_camera_resource()
        detection_active_event.clear()
        T.info("Detection thread exited. Resetting detection_active_event.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        global manual_shutdown_requested
        if not manual_shutdown_requested:
            from notifications import send_telegram_error_alert
            send_telegram_error_alert(
                f"⚠️ Detection thread exited at {timestamp}. Motion detection is OFF."
            )
        else:
            T.info("Detection stopped manually — no Telegram error alert sent.")
         
        # ✅ Always reset shutdown flags
        manual_shutdown_requested = False  # reset for next run
        set_sudo_shutdown_in_progress (False)  # ✅ Reset here
        
        # Thread-safe GUI idle update
        try:
            from gui import gui_exists, enqueue_gui, update_gui_idle_state
            if gui_exists():
                enqueue_gui(update_gui_idle_state)
            else:
                T.info("GUI not active; skipping idle state update.")
        except Exception as e:
            T.warning(f"Idle state dispatch failed: {e}")

        T.info("[✅] Detection thread terminated.")
        detection_thread = None


def launch_detection():
    """Starts the motion detection thread."""
    T.info("launch_detection() called")
    # print("launch_detection() called")
    global detection_thread

    if detection_thread and detection_thread.is_alive():
        T.warning("[!] Detection already running.")
        return

    detection_active_event.set()
    T.info("[▶️] Starting detection thread.")

    
    detection_thread = threading.Thread(target=main, name="DetectionThread", daemon=True)
    detection_thread.start()
    T.info("Motion detection started.")
