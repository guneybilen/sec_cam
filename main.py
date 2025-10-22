# main.py
# import traceback
from PyQt5.QtCore import QSocketNotifier
import sys
import os
import signal
import tracelog as T
import threading
from qasync import QEventLoop
import asyncio
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime

from PyQt5.QtWidgets import QApplication
#from PyQt5.QtCore import QCoreApplication
from PyQt5.QtCore import qInstallMessageHandler

# Save the original __init__
_orig_init = QSocketNotifier.__init__

def debug_init(self, *args, **kwargs):
    # print("\n[DEBUG] QSocketNotifier created!")
    # traceback.print_stack(limit=10)
    return _orig_init(self, *args, **kwargs)

def qt_message_handler(mode, context, message):
    # print(f"[QtMessage] {message} ({context.file}:{context.line})")
    pass
    
qInstallMessageHandler(qt_message_handler)

from gui import create_gui, handle_autostart
from detection import shutdown_detection_pipeline
from telegram_bot import stop_telegram_bot
from telegram_bot import start_telegram_listener_async, stop_telegram_listener_async
from utils import clean_old_clips, schedule_daily_summary
from gui import init_widgets_for_boot

# Global Variables
watchdog_thread = None
watchdog_stop_event = threading.Event() # CRITICAL: Event to signal the watchdog to stop


def setup_signal_handlers():
    """Sets up signal handlers for graceful termination."""
    signal.signal(signal.SIGINT, force_exit)
    signal.signal(signal.SIGTERM, force_exit)

def run_initial_setup():
    """Runs initial setup tasks like cleaning old clips and scheduling daily summary."""
    clean_old_clips()
    schedule_daily_summary()

def initialize_gui():
    """Initializes the GUI."""
    from gui import create_gui
    window = create_gui()
    return window

def run_main_loop(window):
    """Runs the main GUI event loop."""
    from PyQt5.QtWidgets import QApplication
    T.info("Main loop started.")
    if window: # QCoreApplication.instance()
        window.show()
        app = QApplication.instance()
        app.exec_()
    else:
        T.error("GUI initialization failed. Cannot run main loop.")

def force_exit(signum=None, frame=None):
    """Forces immediate termination of the application process."""
    import os
    import sys

    T.info(f"\n[🛑] Received exit signal {signum}. Initiating force exit.")

    # CRITICAL: Attempt to gracefully shutdown the Telegram listener
    # This MUST be done BEFORE the GUI is destroyed to ensure the bot
    # has a chance to cleanly disconnect.
    from telegram_bot import stop_telegram_bot, telegram_thread
    stop_telegram_bot()  # Signals the Telegram thread to stop

    # After signaling the bot to stop, wait for the thread to terminate
    if telegram_thread and telegram_thread.is_alive():
        telegram_thread.join(timeout=5)  # Allow the thread 5 seconds to terminate
        if telegram_thread.is_alive():
            T.warning("Telegram thread failed to terminate within timeout.")

    # Shutdown GUI (must be done before releasing camera)
    from gui import shutdown_gui
    shutdown_gui()

    # Release camera resources
    from detection import release_camera_resource
    release_camera_resource()

    # Finally, exit the system
    T.info("[💀] Forcing system exit.")
    sys.exit(0)

def run_watchdog():
    """
    Watchdog loop that monitors critical background services.
    Respects the global stop event and performs health checks
    on detection and (optionally) Telegram subsystems.
    """
    global watchdog_thread, watchdog_stop_event

    from detection import launch_detection, detection_thread, detection_active_event
    from telegram_bot import telegram_bot_running  # async flag only

    T.debug("[👁️] Watchdog started.")

    while not watchdog_stop_event.is_set():
        # ---------------------------------------------------------
        # 1. Check Telegram bot health (async version)
        # ---------------------------------------------------------
        if not telegram_bot_running:
            T.warning("Telegram bot not running (async). Manual restart may be required.")

        # ---------------------------------------------------------
        # 2. Check Detection thread health
        # ---------------------------------------------------------
        if detection_thread and not detection_thread.is_alive() and detection_active_event.is_set():
            T.error("Detection thread crashed. Restarting...")
            launch_detection()

        # ---------------------------------------------------------
        # 3. Sleep / wait for stop event
        # ---------------------------------------------------------
        watchdog_stop_event.wait(10)  # Wait 10 seconds or until stop event is set

    T.debug("[👁️] Watchdog terminated.")

def main_entry():
    """The main application entry point with qasync integration."""
    global watchdog_thread, watchdog_stop_event
    try:
        # ---------------------------------------------------------
        # 1. Setup system-level handlers and background watchdog
        # ---------------------------------------------------------
        setup_signal_handlers()
        watchdog_thread = threading.Thread(target=run_watchdog, name="WatchdogThread", daemon=True)
        watchdog_thread.start()
        # ---------------------------------------------------------
        # 2. Create QApplication and wrap with qasync loop
        # ---------------------------------------------------------
        app = QApplication(sys.argv)
        loop = QEventLoop(app)
        asyncio.set_event_loop(loop)
        # ---------------------------------------------------------
        # 3. Launch Telegram bot inside the unified asyncio loop
        # ---------------------------------------------------------
        from telegram_bot import start_telegram_listener_async, stop_telegram_listener_async
        loop.create_task(start_telegram_listener_async())
        app.aboutToQuit.connect(lambda: loop.create_task(stop_telegram_listener_async()))
        # ---------------------------------------------------------
        # 4. Initialize GUI and widgets
        # ---------------------------------------------------------
        from gui import initialize_gui, handle_autostart, init_widgets_for_boot
        window = initialize_gui()
        init_widgets_for_boot()
        handle_autostart()
        window.show()
        # ---------------------------------------------------------
        # 5. Run housekeeping tasks (cleanup, scheduling)
        # ---------------------------------------------------------
        from utils import clean_old_clips, schedule_daily_summary
        clean_old_clips()
        schedule_daily_summary()
        # ---------------------------------------------------------
        # 6. Enter the unified Qt + asyncio event loop
        # ---------------------------------------------------------
        with loop:
            T.info("Starting unified Qt/asyncio event loop...")
            loop.run_forever()

    except Exception as e:
        # import traceback
        # T.critical(f"Main GUI execution failed: {e}\n{traceback.format_exc()}")
        T.critical(f"Main GUI execution failed: {e}")
        return

    finally:
        # ---------------------------------------------------------
        # 7. Graceful shutdown of Telegram bot
        # ---------------------------------------------------------
        try:
            loop.run_until_complete(stop_telegram_listener_async())
        except (asyncio.CancelledError, RuntimeError) as e:
            T.warning("Telegram internal tasks cancelled during shutdown (normal).")
        except Exception as e:
            T.warning(f"Error during Telegram shutdown: {e}")
        T.info("Main loop exited cleanly.")


if __name__ == "__main__":
    try:
        print("Don't forget the command is: QT_QPA_PLATFORM=wayland python3 main.py")
        main_entry()
    except SystemExit:
        # Allow clean exits without traceback
        raise
    except Exception as e:
        # import traceback
        # T.critical(f"Unhandled exception at top level: {e}\n{traceback.format_exc()}")
        T.critical(f"Unhandled exception at top level: {e}")

