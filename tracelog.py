# trace.py

import logging
from logging.handlers import TimedRotatingFileHandler
import subprocess
import threading
import time
import os
import sys

USE_COLORS = sys.stdout.isatty()

COLORS = {
    "RESET": "\033[0m",
    # Levels
    "DEBUG": "\033[90m",
    "INFO": "\033[92m",
    "WARNING": "\033[93m",
    "ERROR": "\033[91m",
    "CRITICAL": "\033[95m",
    # Threads
    "DetectionThread": "\033[94m",
    "WatchdogThread": "\033[93m",
    "AlertLoopThread": "\033[95m",
    "DailySummaryWorker": "\033[92m",
    "DailySummaryDelay": "\033[96m",
}

class ColorFormatter(logging.Formatter):
    def format(self, record):
        if USE_COLORS:
            reset = COLORS["RESET"]
            level_colors = {
                "DEBUG": "\033[90m",
                "INFO": "\033[92m",
                "WARNING": "\033[93m",
                "ERROR": "\033[91m",
                "CRITICAL": "\033[95m",
            }
            thread_colors = {
                "DetectionThread": "\033[94m",
                "WatchdogThread": "\033[93m",
                "AlertLoopThread": "\033[95m",
                "DailySummaryWorker": "\033[92m",
                "DailySummaryDelay": "\033[96m",
            }
            record.levelname = f"{level_colors.get(record.levelname, '')}{record.levelname}{reset}"
            record.threadName = f"{thread_colors.get(record.threadName, '')}{record.threadName}{reset}"
        return super().format(record)

# Ensure logs directory exists
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

# === Main daily log ===
main_log_file = os.path.join(LOG_DIR, "motion_detector.log")
file_handler = TimedRotatingFileHandler(
    main_log_file,
    when="midnight",
    interval=1,
    backupCount=7,
    encoding="utf-8"
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(
    "[%(asctime)s] [%(levelname)s] [%(threadName)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))

# === Error-only daily log ===
error_log_file = os.path.join(LOG_DIR, "motion_detector_error.log")
error_handler = TimedRotatingFileHandler(
    error_log_file,
    when="midnight",
    interval=1,
    backupCount=14,
    encoding="utf-8"
)
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(logging.Formatter(
    "[%(asctime)s] [%(levelname)s] [%(threadName)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))

# === Console handler with colors ===
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(ColorFormatter(
    "[%(asctime)s] [%(levelname)s] [%(threadName)s] %(message)s",
    datefmt="%H:%M:%S"
))

# === Root logger ===
logging.getLogger("telegram.ext._application").setLevel(logging.WARNING)

logger = logging.getLogger("motion_detector")
logger.setLevel(logging.ERROR)
logger.addHandler(console_handler)
logger.addHandler(file_handler)
logger.addHandler(error_handler)

# === Convenience wrappers ===
def info(msg): logger.info(msg)
def warning(msg): logger.warning(msg)
def debug(msg): logger.debug(msg)
def error(msg):
    logger.error(msg)
    #_start_repeating_alert(f"Error detected: {msg}")
    #_launch_gui()
def critical(msg):
    logger.critical(msg)
    #_start_repeating_alert(f"Critical error detected: {msg}")
    #_launch_gui()

# === GUI + alert helpers ===
def _launch_gui():
    if os.path.exists("/tmp/gui_ack_active"):
        logger.info("Roger GUI already active — skipping launch.")
        return
    open("/tmp/gui_ack_active", "w").close()
    logger.info("Launching Roger GUI...")
    try:
        subprocess.Popen(["python3", "/home/bilen/Programs/python_programs/sec_cam/ack_gui.py"])
        logger.info("Roger GUI launched.")
    except Exception as e:
        logger.warning(f"[GUI FAIL] {e}")

def _speak(text):
    try:
        subprocess.run(["espeak", "-v", "en+f3", "-s", "120", text], check=True)
    except Exception as e:
        print(f"[SPEAK FAIL] Command failed: {e}")
        
def _start_repeating_alert(text):
    def alert_loop():
        escalation = 0
        while not os.path.exists("/tmp/roger_ack"):
            volume = min(200 + escalation * 50, 500)
            try:
                subprocess.run([
                    "espeak", "-v", "en+f3", "-s", "120",
                    "-a", str(volume),
                    text
                ], check=True)
            except Exception as e:
                logger.error(f"[SPEAK FAIL] {e}")
            time.sleep(5)
            escalation += 1
            if escalation % 6 == 0:
                logger.warning(f"[ESCALATION] Volume increased to {volume}")
    threading.Thread(target=alert_loop, name="AlertLoopThread", daemon=True).start()

def acknowledge():
    with open("/tmp/roger_ack", "w") as f:
        f.write("acknowledged")
    subprocess.run(["espeak", "-v", "en+f3", "-s", "120", "Roger. Error acknowledged."])

