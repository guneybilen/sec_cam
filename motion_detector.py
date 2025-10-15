# ----------------- Imports and Initial Setup -----------------
import cv2
import time
import numpy as np
import os
import requests
import subprocess
import platform
import logging
import threading
import asyncio
import signal
import tracemalloc
import tkinter as tk
from tkinter import messagebox
from datetime import datetime, timedelta
from dotenv import load_dotenv
from moviepy import VideoFileClip # Import from moviepy.editor
from threading import Thread, Timer
from logging.handlers import TimedRotatingFileHandler
from telegram.ext import Application, CommandHandler

# Global state variables
tracemalloc.start()
detection_active = False # Start as False until started by GUI/Telegram
gui_active = True
daily_summary_enabled = True
detection_thread = None
telegram_thread = None # Initialize globally
cap = None
telegram_app = None
telegram_loop = None # We will now manage this manually for the background thread
shutdown_event = threading.Event()
# Tkinter objects initialized to None, will be set inside create_gui
root = None
start_button = None
stop_button = None
cooldown_label = None
on_close = None 
autostart_enabled = True  # Set to False to disable autostart
autostart_status_label = None
autostart_animation_active = False


# ----------------- Configuration and Logging -----------------
log_handler = TimedRotatingFileHandler(
    "motion_log.txt", when="midnight", interval=1, backupCount=7
)

logging.basicConfig(
    level=logging.INFO,
    handlers=[log_handler],
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logging.info("App started")

dotenv_path = os.path.join(os.getcwd(), ".env")
if not os.path.exists(dotenv_path):
    # Fallback for when the .desktop file's Path setting is ignored or failed
    dotenv_path = "/opt/motion-detector/.env"

load_dotenv(dotenv_path=dotenv_path)

autostart_raw = os.getenv("AUTOSTART_ENABLED", "False")
autostart_enabled = autostart_raw.strip().lower() == "true"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ----------------- Utility Functions (Global Scope) -----------------

def update_cooldown_label(seconds_left):
    """Updates the Tkinter cooldown label thread-safely."""
    if not detection_active or root is None or not root.winfo_exists():
        return
    
    # Use root.after to run the GUI update in the main thread
    root.after(
        0, lambda s=seconds_left: cooldown_label.config(text=f"Cooldown: {s}s")
    )
    
def animate_autostart_indicator():
    global autostart_animation_active

    if not autostart_enabled or not autostart_animation_active:
        return  # Stop animation if disabled

    if not root or not root.winfo_exists():
        return

    current_color = autostart_status_label.cget("bg")
    next_color = "limegreen" if current_color == "green" else "green"
    autostart_status_label.config(bg=next_color)

    # Schedule next blink
    root.after(500, animate_autostart_indicator)
    
def stop_autostart_animation():
    global autostart_animation_active
    autostart_animation_active = False
    autostart_status_label.config(bg="green")  # Set to solid green
    logging.info("Autostart animation stopped after 10 seconds.")
        
def send_telegram_error_alert(message):
    """Sends an error message to Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Telegram credentials missing for error alert.")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": f"[ERROR] {message}"},
        )
        logging.info(f"Sent Telegram error alert: {message}")
    except Exception as e:
        logging.error(f"Failed to send Telegram error alert: {e}")

def send_telegram_alert(message="Motion detected!", video_path=None):
    """Sends a motion notification and optional video to Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Telegram credentials missing for motion alert.")
        return

    # 1. Send Text Message
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}

    try:
        requests.post(url, data=data)
        print("[✔] Telegram text alert sent.")
    except Exception as e:
        print(f"[!] Failed to send Telegram message: {e}")

    # 2. Send Video (if provided)
    if video_path and os.path.exists(video_path):
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo"
        try:
            with open(video_path, "rb") as video:
                files = {"video": video}
                data = {"chat_id": TELEGRAM_CHAT_ID, "caption": "Motion detected!"}
                requests.post(url, data=data, files=files)
                print("[✔] Telegram video alert sent.")
        except Exception as e:
            print(f"[!] Failed to send Telegram video: {e}")

def save_clip(cap_instance, duration=5, fps=20):
    """Records a video clip from the camera."""
    os.makedirs("clips", exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    avi_path = f"clips/motion_{timestamp}.avi"

    if not cap_instance.isOpened():
        print("[!] Failed to access webcam during clip save.")
        return None

    width = int(cap_instance.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap_instance.get(cv2.CAP_PROP_FRAME_HEIGHT))
    # Use DIVX for better compatibility and smaller size than XVID often
    fourcc = cv2.VideoWriter_fourcc(*"DIVX")
    out = cv2.VideoWriter(avi_path, fourcc, fps, (width, height))

    start_time = time.time()
    frames_recorded = 0

    while time.time() - start_time < duration:
        if not detection_active:
            print("[🛑] Aborting clip save due to shutdown request.")
            break

        ret, frame = cap_instance.read()
        if not ret:
            break

        out.write(frame)
        frames_recorded += 1
        
        #Display the live view temporarily during recording
        cv2.imshow("Live View", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    out.release()
    cv2.destroyAllWindows()
    
    if frames_recorded > 0:
        print(f"[✔] Saved motion clip with {frames_recorded} frames to {avi_path}")
        return avi_path
    else:
        # Clean up the empty file if recording failed
        if os.path.exists(avi_path):
             os.remove(avi_path)
        print("[!] Clip save failed (0 frames recorded).")
        return None


def compress_video(input_path, target_size_mb=25):
    """Compresses the AVI video to MP4 using moviepy."""
    if not input_path or not os.path.exists(input_path):
        print("[!] Input video path is invalid or missing.")
        return None

    # Wait briefly to ensure file handle is released
    time.sleep(0.5)

    output_path = input_path.replace(".avi", ".mp4")

    try:
        clip = VideoFileClip(input_path)
        
        # Calculate target bitrate based on duration (assuming constant quality needed)
        # 1MB = 8 megabits (Mbps)
        # Bitrate (Mbps) = (Target Size (MB) * 8) / Duration (seconds)
        duration = clip.duration
        target_bitrate_kbps = (target_size_mb * 8 * 1024) / duration

        clip.write_videofile(
            output_path, 
            codec='libx264',  # Use H.264 codec
            bitrate=f"{int(target_bitrate_kbps)}k", 
            logger=None, 
            audio_codec='aac' # Include audio codec even if silent
        )
        clip.close()
        os.remove(input_path)
        print(f"[✔] Compressed and converted to MP4: {output_path}")
        return output_path
    except Exception as e:
        print(f"[!] moviepy compression failed: {e}. Retaining original AVI.")
        return input_path # Return AVI path if compression fails

def send_daily_summary():
    """Compiles and sends a summary of motion events for the day."""
    try:
        with open("motion_log.txt", "r") as f:
            lines = f.readlines()

        today = datetime.now().strftime("%Y-%m-%d")
        summary_lines = [
            line for line in lines if today in line and "Motion detected" in line
        ]

        if summary_lines:
            # Show the last 10 detections
            summary = f"📹 Motion Summary for {today} (Last 10 Events):\n" + "".join(
                summary_lines[-10:]
            )
        else:
            summary = f"📹 No motion detected on {today}."

        send_telegram_alert(summary)
        logging.info("Daily summary sent via Telegram.")

    except Exception as e:
        logging.error(f"Failed to send daily summary: {e}")

def run_and_reschedule_summary():
    """Runs the summary and schedules the next run."""
    send_daily_summary()
    schedule_daily_summary()

def schedule_daily_summary():
    """Schedules the daily summary to run at midnight."""
    if not daily_summary_enabled:
        logging.info("Daily summary disabled. Skipping schedule.")
        return

    now = datetime.now()
    # Schedule for 23:59:00
    next_run = now.replace(hour=23, minute=59, second=0, microsecond=0)

    if next_run < now:
        next_run += timedelta(days=1)

    delay = (next_run - now).total_seconds()
    
    # Start the Timer in a separate thread
    Timer(delay, run_and_reschedule_summary).start()
    logging.info(f"Daily summary scheduled for: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")


def clean_old_clips(folder="clips", days=7):
    """Deletes video clips older than a specified number of days."""
    if not os.path.exists(folder):
        print(f"[🧹] Clip folder '{folder}' does not exist. Skipping cleanup.")
        return
        
    now = time.time()
    cutoff = now - (days * 86400) # 7 days in seconds
    logging.info(f"Starting cleanup of clips older than {days} days.")
    
    for filename in os.listdir(folder):
        filepath = os.path.join(folder, filename)
        if os.path.isfile(filepath):
            try:
                if os.path.getmtime(filepath) < cutoff:
                    os.remove(filepath)
                    print(f"[🧹] Deleted old clip: {filename}")
            except Exception as e:
                print(f"[!] Failed to delete {filename}: {e}")

# ----------------- Thread and GUI Control Functions -----------------

def run_launch_detection_on_main_thread():
    """Queues launch_detection to run in the main thread using Tkinter's event loop."""
    if root and root.winfo_exists():
        root.after(0, launch_detection)
        logging.info("Scheduled launch_detection on main (Tkinter) thread.")
    else:
        # Fallback for headless mode (GUI not running)
        logging.warning("GUI not running, attempting direct launch_detection call.")
        launch_detection()
        
def run_remote_stop_detection_on_main_thread():
    """Queues remote_stop_detection to run in the main thread using Tkinter's event loop."""
    if root and root.winfo_exists():
        root.after(0, remote_stop_detection)
        logging.info("Scheduled remote_stop_detection on main (Tkinter) thread.")
    else:
        # Fallback for headless mode (GUI not running)
        logging.warning("GUI not running, attempting direct remote_stop_detection call.")
        remote_stop_detection()
        
def launch_detection():
    """Starts the motion detection thread."""
    global detection_active, detection_thread 
    
    if detection_thread and detection_thread.is_alive():
        print("[!] Detection already running.")
        return

    detection_active = True
    print("[▶️] Starting detection thread.")

    # Update GUI state (safe because this function is always called on main thread)
    if root and root.winfo_exists():
        start_button.config(state="disabled")
        stop_button.config(state="normal")
        cooldown_label.config(text="Detecting...")

    # Start the thread
    detection_thread = Thread(target=main, daemon=True)
    detection_thread.start()
    logging.info("Motion detection started.")

def remote_stop_detection():
    """Gracefully stops detection WITHOUT a confirmation dialog (for remote use)."""
    global detection_active, cap
    
    detection_active = False
    print("[🛑] Remote stop flag set to False. Waiting for thread exit...")

    # Release camera resource immediately
    if cap is not None and cap.isOpened():
        cap.release()
        print("[✅] Camera resource released by remote_stop_detection.")
        time.sleep(1.0) 
        
    # Wait for the detection thread to finish
    if detection_thread and detection_thread.is_alive():
        detection_thread.join(timeout=5)

    # Update GUI state (safe because this function is always called on main thread)
    if root and root.winfo_exists():
        cooldown_label.config(text="Idle (Remote Stop)")
        stop_button.config(state="disabled")
        start_button.config(state="normal")
        
    logging.info("Motion detection remotely stopped.")
    cap = None 
    
def stop_detection():
    """Gracefully stops the detection thread (with GUI confirmation)."""
    # Only need to declare globals that are assigned (detection_active, cap)
    global detection_active, cap
    
    # This must run on the main thread!
    answer = messagebox.askyesno(
        "Confirm Stop", "Are you sure you want to stop detection?"
    )
    if not answer:
        print("[↩️] Stop canceled.")
        return

    detection_active = False
    print("[🛑] Detection flag set to False. Waiting for thread exit...")

    # Release camera resource immediately to unblock main thread's cap.read()
    if cap is not None and cap.isOpened():
        cap.release()
        print("[✅] Camera resource released by stop_detection.")
        # Give driver time to release
        time.sleep(1.0) 
        
    # Wait for the detection thread to finish
    if detection_thread and detection_thread.is_alive():
        detection_thread.join(timeout=5)
        if detection_thread.is_alive():
            logging.warning("Detection thread failed to join gracefully after 5s.")

    # Update GUI state (safe because this is only called from a GUI button)
    if root and root.winfo_exists():
        cooldown_label.config(text="Idle")
        stop_button.config(state="disabled")
        start_button.config(state="normal")
        
    logging.info("Motion detection stopped.")
    
    # Ensure cap is reset after release
    cap = None 

# ----------------- Telegram Thread Setup (Fix for set_wakeup_fd) -----------------

def start_telegram_thread_sync():
    """
    Synchronous wrapper to manually create and run the asyncio event loop 
    for the Telegram listener in a background thread.
    """
    global telegram_loop
    
    # 1. Create and set a new event loop for this thread
    telegram_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(telegram_loop)
    
    print("[✅] Telegram loop created and set in background thread.")

    try:
        # 2. Run the async listener coroutine until it completes (when polling stops)
        telegram_loop.run_until_complete(run_telegram_listener())
    except Exception as e:
        print(f"[❌] Exception in Telegram loop thread: {e}")
        logging.error(f"Telegram loop failed: {type(e).__name__}: {e}")
    finally:
        # 3. Cleanup the loop
        if telegram_loop and telegram_loop.is_running():
            telegram_loop.stop()
        telegram_loop.close()
        telegram_loop = None # Reset global loop reference
        print("[✅] Telegram listener loop terminated.")


def force_exit(signum=None, frame=None):
    """
    Forces immediate termination of the application process. 
    Can be used as a signal handler.
    """
    # Only need to declare globals that are assigned (detection_active, cap)
    global detection_active, cap 

    print(f"\n[🛑] Received exit signal {signum}. Initiating force exit.")
    
    # 1. Signal detection thread to stop
    detection_active = False
    
    # 2. Signal Telegram listener to stop gracefully via the loop
    if telegram_app and telegram_loop and telegram_loop.is_running():
        print("[🛑] Scheduling Telegram application stop...")
        # Schedule the async stop method to run on the Telegram event loop
        # We use run_coroutine_threadsafe to communicate across threads safely
        asyncio.run_coroutine_threadsafe(telegram_app.stop(), telegram_loop)
        
    if telegram_thread and telegram_thread.is_alive():
        print("[🛑] Waiting for Telegram thread to shut down...")
        # Now the thread should exit when telegram_app.stop() is processed
        telegram_thread.join(timeout=10) 

    # 3. Release camera resource
    if cap is not None and 'isOpened' in dir(cap) and cap.isOpened():
        print("[📷] Releasing camera resource before force exit.")
        cap.release()
        cap = None
    
    # 4. Attempt final Tkinter cleanup
    if root and root.winfo_exists():
        root.destroy()

    print("[💀] Forcing system exit.")
    # This line immediately terminates the entire Python process.
    os._exit(0)

# ----------------- Telegram Listener -----------------

async def start_command(update, context):
    """Handles the /start_detector command from Telegram."""
    chat_id = update.message.chat_id
    
    # --- CRITICAL DEBUGGING ---
    print(f"\n[❓] Received /start_detector from chat ID: {chat_id}")
    try:
        expected_id = int(TELEGRAM_CHAT_ID)
        print(f"[❓] Expected authorized chat ID from .env: {expected_id}")
    except (TypeError, ValueError):
        expected_id = -1 
        print("[❌] TELEGRAM_CHAT_ID in .env is invalid or missing.")
    # --- END DEBUGGING ---

    # Basic authorization check
    if chat_id == expected_id:
        print("[✅] Chat ID Authorized.")
        if detection_active and detection_thread and detection_thread.is_alive():
            await update.message.reply_text("Motion detection is already running.")
            return

        # Use the thread-safe wrapper to queue the function call to the main Tkinter thread
        run_launch_detection_on_main_thread() 

        await update.message.reply_text(
            "Motion detector started remotely. Watch for alerts!"
        )
    else:
        print("[❌] Chat ID Unauthorized.")
        logging.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
        await update.message.reply_text(
            f"Unauthorized access. Your chat ID ({chat_id}) is being logged. Expected ID: {expected_id}"
        )
        
async def stop_command(update, context):
    """Handles the /stop_detector command from Telegram."""
    chat_id = update.message.chat_id
    
    print(f"\n[❓] Received /stop_detector from chat ID: {chat_id}")
    try:
        expected_id = int(TELEGRAM_CHAT_ID)
    except (TypeError, ValueError):
        expected_id = -1 

    # Basic authorization check
    if chat_id == expected_id:
        print("[✅] Chat ID Authorized.")
        if not detection_active:
            await update.message.reply_text("Motion detection is already idle.")
            return

        # Use the thread-safe wrapper to queue the function call to the main Tkinter thread
        run_remote_stop_detection_on_main_thread() 

        await update.message.reply_text(
            "Motion detector stopped remotely."
        )
    else:
        print("[❌] Chat ID Unauthorized.")
        logging.warning(f"Unauthorized stop attempt from chat ID: {chat_id}")
        await update.message.reply_text(
            f"Unauthorized access. Your chat ID ({chat_id}) is being logged."
        )


async def run_telegram_listener():
    """
    Runs the Telegram bot listener in a separate thread's event loop.
    """
    # Only need to declare globals that are assigned (telegram_app)
    global telegram_app 

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error(
            "Telegram listener skipped: TELEGRAM_TOKEN or TELEGRAM_CHAT_ID is missing from .env"
        )
        return

    try:
        # 1. Build the application
        telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()
        telegram_app.add_handler(CommandHandler("start_detector", start_command))
        telegram_app.add_handler(CommandHandler("stop_detector", stop_command)) # New handler
        
        # FIX: Explicitly initialize the application state before starting polling.
        await telegram_app.initialize()
        
        # 2. Start the application (starts the polling coroutine)
        await telegram_app.start()
        print("[✅] Telegram polling started successfully in background thread.")

        # ✅ Start polling explicitly
        await telegram_app.updater.start_polling()

        # 3. Wait forever using an empty Future. This keeps the loop active until 
        # telegram_app.stop() is called from force_exit, which cancels this future.
        await asyncio.Future() 

    except asyncio.CancelledError:
        # This is the expected way the loop terminates when telegram_app.stop() is called.
        print("[✅] Telegram listener loop received cancellation signal (graceful shutdown).")
        
    except Exception as e:
        print(f"[❌] Exception in Telegram listener: {e}")
        logging.error(f"Telegram listener failed: {type(e).__name__}: {e}")
        
    finally:
        # 4. Stop the application cleanly
        if telegram_app and telegram_app.running:
            await telegram_app.stop()
        print("[✅] Telegram application stopped.")


# ----------------- Main Detection Loop -----------------

def main():
    logging.info("main() motion detection loop started.")

    """The main motion detection loop run in a background thread."""
    # Only need to declare global that is assigned (cap)
    global cap,detection_active

    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("[!] Webcam not accessible. Cannot start detection.")
        logging.warning("Camera failed to open during autostart.")
        cap = None
        # Use root.after to update GUI status in the main thread
        if root and root.winfo_exists():
             root.after(0, lambda: cooldown_label.config(text="Camera Error"))
             root.after(0, lambda: start_button.config(state="normal"))
        return

    recording_in_progress = False
    last_alert_time = 0
    cooldown = 30 # seconds

    try:
        while detection_active:
            
            ret, frame1 = cap.read()
            # Reduce CPU load by adding a small sleep
            time.sleep(0.05) 
            ret, frame2 = cap.read()

            if not ret:
                time.sleep(1) # Wait if a frame is missed
                continue

            diff = cv2.absdiff(frame1, frame2)
            gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
            blur = cv2.GaussianBlur(gray, (5, 5), 0)
            _, thresh = cv2.threshold(blur, 20, 255, cv2.THRESH_BINARY)
            
            # Simple motion detection logic
            if np.sum(thresh) > 100000:
                now = time.time()

                if not recording_in_progress and now - last_alert_time > cooldown:
                    
                    # 1. Start Recording
                    recording_in_progress = True
                    # Use the globally defined cap instance
                    avi_file = save_clip(cap) 

                    if avi_file:
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        logging.info(
                            f"Motion detected at {timestamp}, saved clip: {avi_file}"
                        )

                        # 2. Compress and Alert
                        mp4_file = compress_video(avi_file)
                        
                        try:
                            send_telegram_alert("Motion detected!", mp4_file)
                        except Exception as e:
                            error_msg = f"[CRITICAL] Motion pipeline failed: {e}"
                            logging.error(error_msg)
                            send_telegram_error_alert(error_msg)

                        # 3. Start Cooldown
                        last_alert_time = time.time()
                        
                        for i in range(cooldown, 0, -1):
                            if not detection_active:
                                break
                            
                            # Update GUI thread-safely
                            update_cooldown_label(i)
                            time.sleep(1)

                        # 4. End Cooldown
                        if detection_active and root and root.winfo_exists():
                            update_cooldown_label(0)
                            # Re-enable Start button via main thread's root.after (if needed)
                            # The loop starts over to check for motion again
                        
                    recording_in_progress = False
                    
                elif recording_in_progress:
                    print("[⏳] Motion detected but already recording.")
                else:
                     print("[⏳] Motion detected but cooldown is active.")
            
            # Keep the loop running smoothly
            # time.sleep(0.1) # Small delay is already inside the loop's cap.read calls

    except Exception as e:
        # Catch any unexpected errors that break the loop
        logging.error(f"Detection thread crashed: {e}")

    finally:
        # Clean up camera and status when the thread exits
        if cap is not None and 'isOpened' in dir(cap) and cap.isOpened():
            print("[📷] Releasing camera resource at thread exit.")
            cap.release()
            cap = None
            
        # ✅ Add this line to reset the flag
        detection_active = False
        logging.info("Detection thread exited. Resetting detection_active to False.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        send_telegram_error_alert(f"⚠️ Detection thread exited at {timestamp}. Motion detection is OFF.")

        # Update GUI status if still active
        if root and root.winfo_exists():
            root.after(0, lambda: cooldown_label.config(text="Idle"))
            root.after(0, lambda: stop_button.config(state="disabled"))
            root.after(0, lambda: start_button.config(state="normal"))
            
        print("[✅] Detection thread terminated.")


# ----------------- GUI Creation -----------------

def create_gui():
    """Creates the Tkinter GUI widgets and exposes them globally."""
    
    # 1. Define GUI specific commands
    def toggle_daily_summary():
        global daily_summary_enabled
        daily_summary_enabled = not daily_summary_enabled
        status = "enabled" if daily_summary_enabled else "disabled"
        logging.info(f"Daily summary {status} by user.")
        messagebox.showinfo("Daily Summary", f"Daily summary has been {status}.")

    def clear_logs():
        answer = messagebox.askyesno(
            "Confirm", "Are you sure you want to clear the log file?"
        )
        if answer:
            try:
                with open("motion_log.txt", "w") as f:
                    f.write("")
                logging.info("Log file cleared by user.")
                print("[🧹] Log file cleared.")
            except Exception as e:
                logging.error(f"Failed to clear log file: {e}")
                messagebox.showerror("Error", f"Could not clear log file:\n{e}")

    def send_summary_now():
        send_daily_summary()
        messagebox.showinfo("Summary Sent", "Motion summary has been sent.")

    def open_clips_folder():
        folder_path = os.path.abspath("clips")
        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(folder_path)
            elif system == "Darwin":  # macOS
                subprocess.Popen(["open", folder_path])
            else:  # Linux
                subprocess.Popen(["xdg-open", folder_path])
            print("[📂] Opened clips folder.")
        except Exception as e:
            print(f"[!] Failed to open folder: {e}")

    def open_log_file():
        log_path = os.path.abspath("motion_log.txt")
        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(log_path)
            elif system == "Darwin":
                subprocess.Popen(["open", log_path])
            else:
                subprocess.Popen(["xdg-open", log_path])
            print("[📖] Opened log file.")
        except Exception as e:
            print(f"[!] Failed to open log file: {e}")
    
    # Use the global force_exit as the on_close handler
    on_close = force_exit 

    # 2. Create Root Window
    new_root = tk.Tk()
    new_root.title("Motion Detector")
    
    def toggle_autostart():
        global autostart_enabled
        autostart_enabled = not autostart_enabled
        status = "enabled" if autostart_enabled else "disabled"
        logging.info(f"Autostart {status} by user.")
        messagebox.showinfo("Autostart", f"Autostart has been {status}.")

        # Update label
        autostart_status_label.config(
            text=f"Autostart: {'ON' if autostart_enabled else 'OFF'}",
            bg="green" if autostart_enabled else "red"
        )

        # ✅ Write back to .env
        try:
            with open(dotenv_path, "r") as f:
                lines = f.readlines()

            with open(dotenv_path, "w") as f:
                updated = False
                for line in lines:
                    if line.startswith("AUTOSTART_ENABLED="):
                        f.write(f"AUTOSTART_ENABLED={'True' if autostart_enabled else 'False'}\n")
                        updated = True
                    else:
                        f.write(line)
                if not updated:
                    f.write(f"AUTOSTART_ENABLED={'True' if autostart_enabled else 'False'}\n")
            logging.info(".env updated with new autostart state.")
        except Exception as e:
            logging.error(f"Failed to update .env: {e}")

    # 3. Create and pack widgets
    toggle_button = tk.Button(
        new_root,
        text="Toggle Daily Summary",
        font=("Arial", 12),
        command=toggle_daily_summary,
    )
    toggle_button.pack(pady=5)

    # Note: Using the global variables defined at the top
    start_button = tk.Button(
        new_root, text="Start Detection", font=("Arial", 14), command=launch_detection
    )
    start_button.pack(padx=20, pady=10)

    # The stop button still calls the stop_detection with the confirmation dialog
    stop_button = tk.Button(
        new_root, text="Stop Detection", font=("Arial", 14), command=stop_detection, state="disabled"
    )
    stop_button.pack(padx=20, pady=10)

    view_button = tk.Button(
        new_root, text="View Clips", font=("Arial", 12), command=open_clips_folder
    )
    view_button.pack(pady=5)

    log_button = tk.Button(
        new_root, text="View Logs", font=("Arial", 12), command=open_log_file
    )
    log_button.pack(pady=5)

    clear_log_button = tk.Button(
        new_root, text="Clear Logs", font=("Arial", 12), command=clear_logs
    )
    clear_log_button.pack(pady=5)

    summary_button = tk.Button(
        new_root, text="Send Summary Now", font=("Arial", 12), command=send_summary_now
    )
    summary_button.pack(pady=5)
    
    autostart_button = tk.Button(
        new_root, text="Toggle Autostart", font=("Arial", 12), command=toggle_autostart)
    autostart_button.pack(pady=5)
    
    cooldown_label = tk.Label(new_root, text="Ready", font=("Arial", 12))
    cooldown_label.pack(pady=5)
    
    autostart_status_label = tk.Label(
        new_root,
        text=f"Autostart: {'ON' if autostart_enabled else 'OFF'}",
        font=("Arial", 12),
        bg="green" if autostart_enabled else "red",
        fg="white",
        width=20
    )
    autostart_status_label.pack(pady=5)
    autostart_status_label.config(text=f"Autostart: {'ON' if autostart_enabled else 'OFF'}")

    
    # 4. Expose locally created objects to the global scope
    # This is done using globals() dictionary access
    globals()['root'] = new_root
    globals()['start_button'] = start_button
    globals()['stop_button'] = stop_button
    globals()['cooldown_label'] = cooldown_label
    globals()['on_close'] = on_close
    globals()['autostart_status_label'] = autostart_status_label

    return new_root


# ----------------- Main Program Entry Point -----------------

if __name__ == "__main__":

    # Set up signal handlers for graceful exit (uses force_exit)
    signal.signal(signal.SIGINT, force_exit)
    signal.signal(signal.SIGTERM, force_exit)

    # 1. Initialize and start the telegram thread using the new synchronous wrapper
    try:
        telegram_thread = Thread(target=start_telegram_thread_sync, daemon=True)
        telegram_thread.start()
        print("[✅] Telegram listener thread started.")
    except Exception as e:
        print(f"[!] Failed to start Telegram thread: {e}")


    # 2. Initial setup tasks
    clean_old_clips()
    schedule_daily_summary()

    try:
        # 3. Create and configure the GUI.
        root = create_gui()

        # Register the protocol handler (MUST be before mainloop)
        root.protocol("WM_DELETE_WINDOW", on_close)

        # 4. Handle Autostart
        if detection_active:
            # detection_active starts as False. If you want autostart, set it to True 
            # at the top and call launch_detection() here.
            #launch_detection() 
            pass

        # ✅ 4. Handle Autostart — now that GUI is ready
        if autostart_enabled:
            print("[✅] Autostart is ON. Launching detection.")
            # This is the line that was missing!
            launch_detection() 
            
            # Start the indicator animation for a visual cue
            autostart_animation_active = True
            animate_autostart_indicator()

            # Stop animation after 10 seconds
            root.after(10000, lambda: stop_autostart_animation())

        # 5. Start the GUI event loop (the program waits here)
        root.mainloop()

    except Exception as e:
        logging.critical(f"Main GUI execution failed: {e}")
        
    finally:
        gui_active = False 
        print("[✅] Application exit complete.")

