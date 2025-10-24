# gui.py
import cv2
import tracelog as T
import subprocess
import platform
import time
import threading
import asyncio
from qasync import asyncSlot
from qasync import asyncClose
import sys
import os
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QPushButton, QLabel, QVBoxLayout,
    QMessageBox, QTextEdit, QSizePolicy
)
from PyQt5.QtCore import QTimer, Qt, QObject, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtGui import QCloseEvent
from telegram.ext import Application, CommandHandler, ContextTypes
from detection import launch_detection, shutdown_detection_pipeline
from utils import gui_after
from utils import prune_active_timers
from PyQt5.QtCore import QObject, pyqtSignal, QCoreApplication


# Global GUI state variables
app = None
main_window = None
widgets = {}
start_button = None
stop_button = None
cooldown_label = None
autostart_enabled = True  # Set to False to disable autostart
autostart_status_label = None
autostart_animation_active = False
video = None

# Global state variables
gui_active = True
active_timers = []
telegram_status_label = None

class GuiDispatcher(QObject):
    update_signal = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.update_signal.connect(self._execute)

    def _execute(self, func):
        try:
            func()
        except Exception as e:
            import tracelog as T
            T.warning(f"GUI dispatch execution failed: {e}")

# Create a global dispatcher instance
_dispatcher = GuiDispatcher()

gui_queue = asyncio.Queue()

async def gui_queue_worker():
    while True:
        func, args, kwargs = await gui_queue.get()
        try:
            func(*args, **kwargs)
        finally:
            gui_queue.task_done()

# Start this worker after your window is created
# asyncio.create_task(gui_queue_worker())

def enqueue_gui(func, *args, **kwargs):
    """
    Thread-safe: schedule a function to run in the Qt main thread.
    """
    if args or kwargs:
        _dispatcher.update_signal.emit(lambda: func(*args, **kwargs))
    else:
        _dispatcher.update_signal.emit(func)


def update_cooldown_label_threadsafe(seconds_left: int):
    enqueue_gui(update_cooldown_label, seconds_left)

def safe_imshow_threadsafe(frame):
    enqueue_gui(safe_imshow, frame)

def set_cooldown_detecting_threadsafe():
    def _set():
        try:
            widgets["cooldown"].setText("Detecting")
        except Exception as e:
            logging.warning(f"Failed to set cooldown text: {e}")
    enqueue_gui(_set)

def update_gui_idle_state_threadsafe():
    enqueue_gui(update_gui_idle_state)

def show_camera_error_threadsafe():
    enqueue_gui(lambda: cooldown_label.setText("Camera Error"))

def enable_start_button_threadsafe():
    enqueue_gui(lambda: widgets["start"].setEnabled(True))


def stop_telegram_listener():
    """CRITICAL: Signals the Telegram bot application to stop gracefully."""
    from telegram_bot import telegram_thread, telegram_app, telegram_loop
    global telegram_thread, telegram_app, telegram_loop
    if telegram_thread and telegram_thread.is_alive():
        T.info("Signaling Telegram bot to stop...")

        # 1. Stop the application from its own event loop context
        if telegram_app and telegram_loop:
            try:
                # Schedule the async stop method to run on the bot's event loop
                telegram_loop.call_soon_threadsafe(
                    lambda: asyncio.run_coroutine_threadsafe(telegram_app.stop(), telegram_loop))
            except Exception as e:
                T.error(f"Error signaling Telegram application stop: {e}")

        # 2. Join the thread
        telegram_thread.join(timeout=5)
        if telegram_thread and telegram_thread.is_alive():
            T.critical("Telegram thread failed to terminate after signal!")
            return False

        update_telegram_status_label()
        T.info("Telegram listener stopped.")
        return True
    return False

def gui_exists():
    # Return True if a Qt application is running
    return QCoreApplication.instance() is not None

def _set_cooldown_label_text(label, seconds_left):
    if label:
        label.setText(f"Cooldown: {seconds_left}s")

def update_cooldown_label(seconds_left):
    """Updates the cooldown label thread-safely."""
    from detection import detection_active_event
    # print(f"update_cooldown_label function - detection_active_event.is_set(): {detection_active_event.is_set()} and gui_exists(): {gui_exists()}")
    if not detection_active_event.is_set() or not gui_exists() or cooldown_label is None:
        T.info(f"cooldown_label: {cooldown_label}")
        return
    gui_after(0, lambda s=seconds_left: _set_cooldown_label_text(cooldown_label, s))


def _toggle_autostart_label_color(label):
    """Toggles the background color of the autostart status label (Qt version)."""
    if not label:
        return

    current_style = label.styleSheet() or ""
    # Toggle between green and limegreen
    if "limegreen" in current_style:
        label.setStyleSheet("background-color: green;")
    else:
        label.setStyleSheet("background-color: limegreen;")


def animate_autostart_indicator():
    """Animates the autostart indicator by toggling its background color."""
    global autostart_animation_active

    if not autostart_enabled or not autostart_animation_active:
        return

    if not gui_exists():
        return

    _toggle_autostart_label_color(autostart_status_label)

    # Schedule next blink
    gui_after(500, animate_autostart_indicator)


def update_gui_idle_state():
    """Updates GUI components to reflect idle state."""
    if not gui_exists():
        return
    if cooldown_label:
        cooldown_label.setText("Idle")
    if stop_button:
        stop_button.setEnabled(False)
    if start_button:
        start_button.setEnabled(True)


def shutdown_gui():
    """Destroys the GUI window if active."""
    global main_window, app
    if main_window and main_window.isVisible():
        main_window.close()


def stop_autostart_animation():
    global autostart_animation_active
    autostart_animation_active = False
    if autostart_status_label:
        autostart_status_label.setStyleSheet("background-color: green;")  # Set to solid green
    T.info("Autostart animation stopped after 10 seconds.")


def safe_imshow(frame):
    """Thread-safe PyQt5 frame display."""
    global widgets
    # print("[DEBUG] safe_imshow called")

    init_widgets_for_boot()

    if frame is None:
        T.warning("[DEBUG] Frame is None")
        return

    if frame.shape[0] == 0 or frame.shape[1] == 0:
        T.warning(f"safe_imshow received empty frame: shape={frame.shape}")
        return

    if not gui_active or "video_label" not in widgets:
        T.warning("[DEBUG] either gui_active is false or video_label absent in widgets")
        return

    try:
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        #T.debug(f"Converted frame to RGB: shape={frame_rgb.shape}")
        # print(f"Converted frame to RGB: shape={frame_rgb.shape}")
        h, w, ch = frame_rgb.shape
        bytes_per_line = ch * w
        qimg = QImage(frame_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        #T.debug(f"Created QImage: size={qimg.width()}x{qimg.height()}")
        # print(f"Created QImage: size={qimg.width()}x{qimg.height()}")
        pixmap = QPixmap.fromImage(qimg).scaled(
            widgets["video_label"].size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        #T.debug(f"Scaled pixmap to label size: {widgets['video_label'].size()}")

        # print(f"Scaled pixmap to label size: {widgets['video_label'].size()}")

        # ✅ Use Qt's thread-safe signal queue
        def update_pixmap():
            if "video_label" in widgets:
                # print("[DEBUG] update_pixmap triggered")
                widgets["video_label"].setPixmap(pixmap)

        gui_after(0, update_pixmap)

    except Exception as e:
        T.warning(f"Failed to update video frame: {e}")


# ----------------- Thread and GUI Control Functions -----------------

def run_launch_detection_on_main_thread():
    """Queues launch_detection to run in the main thread using Qt's event loop."""
    if gui_exists():
        gui_after(0, launch_detection)
        T.info("Scheduled launch_detection on main (Qt) thread.")
    else:
        T.warning("GUI not running, attempting direct launch_detection call.")
        launch_detection()


def run_remote_stop_detection_on_main_thread():
    """Queues remote_stop_detection to run in the main thread using Qt's event loop."""
    if gui_exists():
        gui_after(0, remote_stop_detection)
        T.info("Scheduled remote_stop_detection on main (Qt) thread.")
    else:
        T.warning("GUI not running, attempting direct remote_stop_detection call.")
        remote_stop_detection()


def remote_stop_detection():
    """Gracefully stops detection WITHOUT a confirmation dialog (for remote use)."""
    shutdown_detection_pipeline(skip_auth=True)


def stop_detection():
    # from PyQt5.QtWidgets import QMessageBox
    if not gui_exists():
        shutdown_detection_pipeline(skip_auth=False)
        return
    # answer = QMessageBox.question(main_window, "Confirm Stop", "Are you sure you want to stop detection?",
    #                              QMessageBox.Yes | QMessageBox.No)
    # if answer != QMessageBox.Yes:
    #     T.info("[↩️] Stop canceled.")
    #     return

    shutdown_detection_pipeline(skip_auth=False)


def build_gui_commands():
    """Builds the dictionary of GUI command functions."""
    from PyQt5.QtWidgets import QMessageBox

    from utils import send_daily_summary
    from utils import clean_old_clips

    def toggle_daily_summary():
        from utils import daily_summary_enabled
        global daily_summary_enabled
        daily_summary_enabled = not daily_summary_enabled
        status = "enabled" if daily_summary_enabled else "disabled"
        T.info(f"Daily summary {status} by user.")
        QMessageBox.information(main_window, "Daily Summary", f"Daily summary has been {status}.")

    def clear_logs():
        answer = QMessageBox.question(main_window, "Confirm", "Are you sure you want to clear the log file?",
                                      QMessageBox.Yes | QMessageBox.No)
        if answer == QMessageBox.Yes:
            try:
                with open("motion_log.txt", "w") as f:
                    f.write("")
                T.info("Log file cleared by user.")
                # print("[🧹] Log file cleared.")
                QMessageBox.information(main_window, "Cleared", "Log file cleared.")
            except Exception as e:
                T.error(f"Failed to clear log file: {e}")
                QMessageBox.critical(main_window, "Error", f"Could not clear log file:\n{e}")

    def send_summary_now():
        send_daily_summary()
        QMessageBox.information(main_window, "Summary Sent", "Motion summary has been sent.")

    def open_clips_folder():
        folder_path = os.path.abspath("clips")
        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(folder_path)
            elif system == "Darwin":
                subprocess.Popen(["open", folder_path])
            else:
                subprocess.Popen(["xdg-open", folder_path])
            T.info("[📂] Opened clips folder.")
        except Exception as e:
            T.error(f"[!] Failed to open folder: {e}")

    def open_log_file():
        """Opens the log file using the default system application."""
        log_path = os.path.abspath("motion_log.txt")
        system = platform.system()

        try:
            if system == "Windows":
                os.startfile(log_path)
            elif system == "Darwin":
                subprocess.Popen(["open", log_path])
            else:
                subprocess.Popen(["xdg-open", log_path])
            T.info("[📖] Opened log file.")

        except OSError as e:
            T.error(f"Failed to open log file with default application: {e}")
            # print(f"[!] Could not open log file automatically. Check error log.")
        except Exception as e:
            T.error(f"An unexpected error occurred: {e}")
            # print(f"[!] An unexpected error occurred. Check error log.")

    return {
        "toggle_daily_summary": toggle_daily_summary,
        "clear_logs": clear_logs,
        "send_summary_now": send_summary_now,
        "open_clips_folder": open_clips_folder,
        "open_log_file": open_log_file,
    }

def build_gui_widgets(commands):
    global start_button, stop_button, cooldown_label, autostart_status_label
    global telegram_status_label, video, widgets

    # --- Control buttons ---
    start_button = QPushButton("Start Detection")
    start_button.clicked.connect(launch_detection)

    stop_button = QPushButton("Stop Detection")
    stop_button.clicked.connect(stop_detection)
    stop_button.setEnabled(False)

    clear_button = QPushButton("Clear Logs")
    clear_button.clicked.connect(commands["clear_logs"])

    summary_button = QPushButton("Send Summary")
    summary_button.clicked.connect(commands["send_summary_now"])

    toggle_summary_button = QPushButton("Toggle Daily Summary")
    toggle_summary_button.clicked.connect(commands["toggle_daily_summary"])

    open_clips_button = QPushButton("Open Clips Folder")
    open_clips_button.clicked.connect(commands["open_clips_folder"])

    open_log_button = QPushButton("Open Log File")
    open_log_button.clicked.connect(commands["open_log_file"])

    # --- Status labels ---
    cooldown_label = QLabel("Idle")
    cooldown_label.setAlignment(Qt.AlignCenter)

    autostart_status_label = QLabel("Autostart")
    autostart_status_label.setStyleSheet("background-color: green; padding:4px;")
    autostart_status_label.setAlignment(Qt.AlignCenter)

    # Telegram help + status
    telegram_help_label = QLabel(
        "Telegram Commands:\n"
        "/start_detector – Start detection\n"
        "/stop_detector – Stop detection\n"
        "/status – System status\n"
        "/summary – Motion summary"
    )
    telegram_help_label.setAlignment(Qt.AlignLeft)
    telegram_help_label.setWordWrap(True)
    telegram_help_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    telegram_status_label = QLabel("Telegram Bot: 🔴 Disconnected")
    telegram_status_label.setObjectName("telegramStatusLabel")
    telegram_status_label.setStyleSheet("color: red; font-weight: bold;")
    telegram_status_label.setAlignment(Qt.AlignCenter)
    widgets["telegram_status_label"] = telegram_status_label
    
    # --- Video preview ---
    video = QLabel()
    video.setAlignment(Qt.AlignCenter)
    video.setFixedSize(640, 480)  # adjust to your camera resolution
    video.setStyleSheet("background-color: black;")

    # --- Collect widgets ---
    widgets = {
        "start": start_button,
        "stop": stop_button,
        "cooldown": cooldown_label,
        "autostart": autostart_status_label,
        "clear": clear_button,
        "summary": summary_button,
        "toggle_summary": toggle_summary_button,
        "open_clips": open_clips_button,
        "open_log": open_log_button,
        "telegram_help": telegram_help_label,
        "telegram_status": telegram_status_label,
        "video_label": video,
    }
    return widgets



class MainWindow(QMainWindow):
    def __init__(self, commands):
        super().__init__()
        self.setWindowTitle("Motion Detector")

        # --- Build central layout ---
        central = QWidget()
        layout = QVBoxLayout()

        global widgets
        build_gui_widgets(commands)

        # Add widgets to layout
        layout.addWidget(widgets["start"])
        layout.addWidget(widgets["stop"])
        layout.addWidget(widgets["cooldown"])
        layout.addWidget(widgets["autostart"])
        layout.addWidget(widgets["clear"])
        layout.addWidget(widgets["summary"])
        layout.addWidget(widgets["toggle_summary"])
        layout.addWidget(widgets["open_clips"])
        layout.addWidget(widgets["open_log"])
        layout.addWidget(widgets["telegram_help"])
        layout.addWidget(widgets["telegram_status"])
        layout.addWidget(widgets["video_label"])
        layout.addStretch()

        central.setLayout(layout)
        self.setCentralWidget(central)
        

    def show_sudo_failure_popup():
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Authorization Failed")
        msg.setText("Sudo authorization failed. Cannot close the application.")
        msg.exec_()

    @asyncClose
    async def closeEvent(self, event: QCloseEvent) -> None:
        """
        Clean shutdown when the GUI window is closed.
        """

        from telegram_bot import telegram_thread, stop_telegram_bot
        from detection import detection_thread, release_camera_resource, shutdown_detection_pipeline
        from detection import detection_active_event
        from main import watchdog_stop_event

        T.info("Closing Application...")
        
        # Attempt secure shutdown
        success = shutdown_detection_pipeline(skip_auth=False)
        print(f"[DEBUG] closeEvent triggered, shutdown success={success}")

        if success:
            # --- 1. Stop Telegram bot ---
            try:
                detection_active_event.clear()
                await stop_telegram_bot()
                T.info("Telegram bot stopped cleanly.")
            except (asyncio.CancelledError, RuntimeError) as e:
                T.warning(f"Asyncio.CancelledError supressed: {e}")
            except Exception as e:
                T.warning(f"Telegram shutdown failed with await: {e}, trying signal...")
                stop_telegram_bot()

	        # --- 2. Cancel all pending asyncio tasks ---
            # current_task = asyncio.current_task()
            # tasks = [t for t in asyncio.all_tasks() if t is not current_task]
            # T.info(f"Cancelling {len(tasks)} pending asyncio tasks...")
            # for t in tasks:
            #    t.cancel()
            # await asyncio.gather(*tasks, return_exceptions=True)

            # --- 3. Join threads ---
            if telegram_thread and telegram_thread.is_alive():
                telegram_thread.join(timeout=5)

            if detection_thread and detection_thread.is_alive():
                detection_thread.join(timeout=5)

            # --- 4. Stop detection and release resources ---
            watchdog_stop_event.set()
            release_camera_resource()

            prune_active_timers()
            for t in active_timers:
                if t.is_alive():
                    T.info(f"[TIMER] Joining active timer: {t}")
                    t.join(timeout=2)

            shutdown_gui()

            T.info("[🛑] Application closed via GUI.")
            event.accept()
            time.sleep(1)
            T.info(f"Active threads at shutdown: {threading.enumerate()}")

        else:
            T.warning("[GUI] Secure shutdown failed. Preventing GUI close.")
            detection_active_event.set()
            event.ignore()
            from gui import enqueue_gui, cooldown_label
            enqueue_gui(lambda: cooldown_label.setText("❌ Sudo failed — cannot exit"))
            show_sudo_failure_popup()

def update_telegram_status_label():
    from telegram_bot import is_telegram_running
    global telegram_status_label
    T.info(f"[DEBUG] update_telegram_status_label called, is_telegram_running={is_telegram_running()}")
    if telegram_status_label:
        if is_telegram_running():
            telegram_status_label.setText("Telegram Bot: 🟢 Connected")
            telegram_status_label.setStyleSheet("color: green; font-weight: bold;")
        else:
            telegram_status_label.setText("Telegram Bot: 🔴 Disconnected")
            telegram_status_label.setStyleSheet("color: darkred; font-weight: bold;")
    else:
        T.info("[DEBUG] telegram_status_label is None!")

def create_gui():
    """
    Build and initialize the main GUI window.
    Ensures a single QApplication instance, sets up widgets,
    and schedules periodic tasks.
    """
    global app, main_window, widgets, on_close

    # 1. Ensure a single QApplication instance
    app = QApplication.instance() or QApplication(sys.argv)
    T.info("GUI launched. Entering main loop.")

    # 2. Build commands, widgets, and main window
    commands = build_gui_commands()
    build_gui_widgets(commands)
    main_window = MainWindow(commands)

    # 3. Initial Telegram status update
    update_telegram_status_label()

    # 4. Periodic tasks
    def schedule_telegram_status_updates():
        update_telegram_status_label()
        if gui_exists():
            gui_after(5000, schedule_telegram_status_updates)

    def schedule_timer_pruning():
        from utils import prune_active_timers
        prune_active_timers()
        if gui_exists():
            gui_after(60000, schedule_timer_pruning)

    schedule_telegram_status_updates()
    schedule_timer_pruning()

    # 5. Finalize
    T.info("GUI created and initialized.")
    return main_window

def handle_autostart():
    """Handles the autostart functionality."""
    from detection import detection_active_event

    if detection_active_event.is_set():
        detection_active_event.set()

    if autostart_enabled:
        T.info("[✅] Autostart is ON. Launching detection.")
        from detection import launch_detection
        launch_detection()

        global autostart_animation_active
        autostart_animation_active = True
        animate_autostart_indicator()
        gui_after(10000, stop_autostart_animation)


def initialize_gui():
    """Initializes the GUI."""
    global main_window
    main_window = create_gui()
    return main_window


def init_widgets_for_boot():
    """Initializes the GUI safely on the main thread."""
    from detection import detection_active_event

    def _init():
        if gui_exists():
            if start_button:
                start_button.setEnabled(False)
            if stop_button:
                stop_button.setEnabled(True)
            if cooldown_label:
                cooldown_label.setText("Detecting...")

    enqueue_gui(_init)


async def stop_detection_command(update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /stop command."""
    from detection import detection_active_event, shutdown_detection_pipeline
    if detection_active_event.is_set():
        shutdown_detection_pipeline(skip_auth=True)
        await update.message.reply_text('Motion detection stopped remotely.')
    else:
        await update.message.reply_text('Motion detection is already stopped.')


async def start_detection_command(update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command."""
    from detection import detection_active_event, launch_detection
    if not detection_active_event.is_set():
        launch_detection(skip_auth=True)
        await update.message.reply_text('Motion detection started remotely.')
    else:
        await update.message.reply_text('Motion detection is already running.')
