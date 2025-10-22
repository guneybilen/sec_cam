# notifications.py
import os
import requests
import tracelog as T
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime
from dotenv import load_dotenv
from threading import Thread
import threading
from utils import compress_video

# Size limits (in bytes)
EMAIL_MAX_SIZE = 20 * 1024 * 1024      # 20 MB
TELEGRAM_MAX_SIZE = 50 * 1024 * 1024   # 50 MB

# Load environment variables
dotenv_path = os.path.join(os.getcwd(), ".env")
if not os.path.exists(dotenv_path):
    # Fallback for when the .desktop file's Path setting is ignored or failed
    dotenv_path = "/opt/motion-detector/.env"

load_dotenv(dotenv_path=dotenv_path)

# Telegram and Email Configuration (Read from .env)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FROM_EMAIL = os.getenv("FROM_EMAIL")
APP_PASSWORD = os.getenv("APP_PASSWORD")
fastmail_recipient = os.getenv("FASTMAIL_RECIPIENT")

# Global Variables
motion_count_today = 0
motion_count_lock = threading.Lock()


def increment_motion_count():
    global motion_count_today
    with motion_count_lock:
        motion_count_today += 1
        return motion_count_today

def reset_motion_count():
    global motion_count_today
    with motion_count_lock:
        motion_count_today = 0

def get_motion_count():
    with motion_count_lock:
        return motion_count_today


def send_fastmail_email_with_attachment(
        subject: str,
        body: str,
        to_email: str,
        video_path: str,
        from_email: str,
        app_password: str
):
    if not to_email:
        T.error("FASTMAIL_RECIPIENT not configured. Skipping email alert.")
        return

    # Check file size before attaching
    if video_path and os.path.exists(video_path):
        file_size = os.path.getsize(video_path)
        if file_size > EMAIL_MAX_SIZE:
            T.warning(f"Video {video_path} is {file_size/1024/1024:.2f} MB, exceeds email limit. Sending text only.")
            video_path = None
    else:
        T.error(f"Video file not found: {video_path}")
        return

    msg = MIMEMultipart()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    if video_path:
        with open(video_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(video_path)}"')
            msg.attach(part)

    try:
        with smtplib.SMTP("smtp.fastmail.com", 587) as server:
            server.starttls()
            server.login(from_email, app_password)
            server.send_message(msg)
        T.info("[✅] Email sent successfully.")
    except Exception as e:
        T.error(f"Failed to send email: {e}")


def send_telegram_error_alert(message):
    """Sends an error message to Telegram."""

    def send_telegram_text(message):
        """Sends an error message to Telegram."""
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            T.error("Telegram credentials missing for error alert.")
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": f"[ERROR] {message}"},
                timeout=60
            )
            T.info(f"Sent Telegram error alert: {message}")
        except Exception as e:
            T.error(f"Failed to send Telegram error alert: {e}")

    Thread(target=send_telegram_text, args=(message,), daemon=True).start()


def send_telegram_alert(message="Motion detected!", video_path=None):
    def send_telegram_alert_text(message, video_path):
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            T.error("Telegram credentials missing for motion alert.")
            return

        # Always send text
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        try:
            requests.post(url, data=data, timeout=60)
            T.info("[✔] Telegram text alert sent.")
        except Exception as e:
            T.error(f"Failed to send Telegram message: {e}")

        # Send video only if under size limit
        if video_path and os.path.exists(video_path):
            file_size = os.path.getsize(video_path)
            if file_size > TELEGRAM_MAX_SIZE:
                T.warning(f"Video {video_path} is {file_size/1024/1024:.2f} MB, exceeds Telegram limit. Skipping video.")
                return
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo"
            try:
                with open(video_path, "rb") as video:
                    files = {"video": video}
                    data = {"chat_id": TELEGRAM_CHAT_ID, "caption": "Motion detected!"}
                    requests.post(url, data=data, files=files, timeout=60)
                    T.info("[✔] Telegram video alert sent.")
            except Exception as e:
                T.error(f"Failed to send Telegram video: {e}")

    Thread(target=send_telegram_alert_text, args=(message, video_path), daemon=True).start()


def send_alerts_async(mp4_file):
    """Sends both Telegram and email alerts asynchronously."""

    def send_telegram(mp4):
        send_telegram_alert("Motion detected!", mp4)

    def send_fastmail(mp4_file):
        if not fastmail_recipient:
            T.error("FASTMAIL_RECIPIENT not configured. Skipping email alert.")
            return
           
        send_fastmail_email_with_attachment(
            subject="Motion Alert: Activity Detected",
            body="Motion was detected. See attached video clip.",
            to_email=fastmail_recipient,
            video_path=mp4_file,
            from_email=FROM_EMAIL,
            app_password=APP_PASSWORD
        )

    Thread(target=send_telegram, args=(mp4_file,), daemon=True).start()
    Thread(target=send_fastmail, args=(mp4_file,), daemon=True).start()
    
    

