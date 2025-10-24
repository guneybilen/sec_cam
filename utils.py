# utils.py
import os
import time
import tracelog as T
import subprocess
from datetime import datetime, timedelta
from contextlib import contextmanager
import cv2
from pathlib import Path
from imageio_ffmpeg import get_ffmpeg_exe

# from PyQt5.QtCore import QTimer, QObject, pyqtSignal

# Global Variables
daily_summary_enabled = True
active_timers = []

from dotenv import load_dotenv
# Load environment variables
dotenv_path = os.path.join(os.getcwd(), ".env")
if not os.path.exists(dotenv_path):
    # Fallback for when the .desktop file's Path setting is ignored or failed
    dotenv_path = "/opt/motion-detector/.env"

load_dotenv(dotenv_path=dotenv_path)

# Telegram and Email Configuration (Read from .env)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# utils.py
def gui_after(ms, func):
    from PyQt5.QtCore import QTimer
    from gui import gui_exists, enqueue_gui   # imported here, not at top

    if gui_exists():
        QTimer.singleShot(ms, lambda: enqueue_gui(func))


def prune_active_timers():
    """
    Removes finished (non-alive) threads from the active_timers list.
    Call this periodically to prevent memory growth.
    """
    global active_timers
    before = len(active_timers)
    active_timers = [t for t in active_timers if t.is_alive()]
    after = len(active_timers)
    if before != after:
        T.info(f"[TIMER] Pruned {before - after} finished timers. {after} still active.")
        

@contextmanager
def open_video_writer(path, fourcc, fps, frame_size):
    """Context manager for opening and releasing the video writer."""
    writer = cv2.VideoWriter(path, fourcc, fps, frame_size)
    try:
        yield writer
    finally:
        if writer:
            writer.release()


def compress_video(input_path, target_size_mb=10):
    """Compresses and converts video to MP4 using imageio-ffmpeg with H.265 codec (Apple-compatible)."""
    if not input_path or not os.path.exists(input_path):
        T.warning("Input video path is invalid or missing.")
        return None

    time.sleep(0.5)  # Ensure file handle is released

    base = str(Path(input_path).with_suffix(''))
    output_path = f"{base}.mp4"
    ffmpeg_path = get_ffmpeg_exe()
    ffprobe_path = "ffprobe"

    try:
        # Get video duration using ffprobe
        probe_cmd = [
            ffprobe_path, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
            input_path
        ]
        duration_str = subprocess.check_output(probe_cmd).decode().strip()
        duration = float(duration_str)

        if duration == 0:
            T.warning("Video duration is zero. Skipping compression.")
            return input_path

        # Calculate target bitrate in kbps
        target_bitrate_kbps = (target_size_mb * 8 * 1024) / duration

        # Build ffmpeg command to write directly to .mp4
        ffmpeg_cmd = [
            ffmpeg_path, "-y", "-i", input_path,
            "-c:v", "libx265", "-tag:v", "hvc1", "-b:v", f"{int(target_bitrate_kbps)}k",
            "-c:a", "aac", "-preset", "medium",
            output_path
        ]

        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Optionally remove original if conversion succeeded
        if input_path != output_path and os.path.exists(output_path):
            os.remove(input_path)
            T.info(f"Compressed and converted to MP4 (H.265): {output_path}")
        return output_path

    except Exception as e:
        T.error(f"FFmpeg compression failed: {e}. Retaining original video.")
        if os.path.exists(output_path):
            os.remove(output_path)
        return input_path


def send_daily_summary():
    """Compiles and sends a summary of motion events for the day."""
    from notifications import send_telegram_alert
    try:
        with open("motion_log.txt", "r") as f:
            lines = f.readlines()

        today = datetime.now().strftime("%Y-%m-%d")
        summary_lines = [
            line for line in lines if today in line and "Motion detected" in line
        ]

        if summary_lines:
            summary = f"📹 Motion Summary for {today} (Last 10 Events):\n" + "".join(
                summary_lines[-10:]
            )
        else:
            summary = f"📹 No motion detected on {today}."

        send_telegram_alert(summary)
        T.info("Daily summary sent via Telegram.")

    except Exception as e:
        T.error(f"Failed to send daily summary: {e}")


def run_and_reschedule_summary():
    """Runs the summary and schedules the next run."""
    from notifications import reset_motion_count
    reset_motion_count()
    send_daily_summary()
    schedule_daily_summary()


def schedule_daily_summary():
    """Schedules the daily summary to run at midnight."""
    global active_timers
    if not daily_summary_enabled:
        T.info("Daily summary disabled. Skipping schedule.")
        return

    now = datetime.now()
    next_run = now.replace(hour=23, minute=59, second=0, microsecond=0)

    if next_run < now:
        next_run += timedelta(days=1)

    delay = (next_run - now).total_seconds()

    def delayed_summary_launcher():
        T.info(f"[TIMER] Sleeping for {delay:.2f} seconds before launching summary.")
        time.sleep(delay)
        T.info("[TIMER] Callback starting.")
        import threading
        threading.Thread(target=run_and_reschedule_summary, name="DailySummaryWorker", daemon=True).start()
        T.info("[TIMER] Callback offloaded.")

        # ✅ Launch the delay thread instead of using Timer
        import threading
        t = threading.Thread(target=delayed_summary_launcher, name="DailySummaryDelay", daemon=True)
        t.start()
        active_timers.append(t)
        prune_active_timers()
        T.info(f"Daily summary scheduled for: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")


def clean_old_clips(folder="./clips", days=7):
    """Deletes video clips older than a specified number of days."""
    if not os.path.exists(folder):
        T.error(f"[🧹] Clip folder '{folder}' does not exist. Skipping cleanup.")
        return

    now = time.time()
    cutoff = now - (days * 86400)  # 7 days in seconds
    T.info(f"Starting cleanup of clips older than {days} days.")

    for filename in os.listdir(folder):
        filepath = os.path.join(folder, filename)
        if os.path.isfile(filepath):
            try:
                if os.path.getmtime(filepath) < cutoff:
                    os.remove(filepath)
                    T.info(f"[🧹] Deleted old clip: {filename}")
            except Exception as e:
                T.error(f"[!] Failed to delete {filename}: {e}")
