# config.py

import os
from dotenv import load_dotenv

def load_config():
    # Determine .env path
    dotenv_path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(dotenv_path):
        dotenv_path = "/opt/motion-detector/.env"

    load_dotenv(dotenv_path=dotenv_path)

    # Load flags and credentials
    autostart_raw = os.getenv("AUTOSTART_ENABLED", "False")
    autostart_enabled = autostart_raw.strip().lower() == "true"

    telegram_token = os.getenv("TELEGRAM_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    fastmail_email = os.getenv("FASTMAIL_EMAIL")
    fastmail_password = os.getenv("FASTMAIL_APP_PASSWORD")
    fastmail_recipient = os.getenv("FASTMAIL_RECIPIENT")
    cooldown_seconds = int(os.getenv("COOLDOWN_SECONDS", "30"))
    motion_score = int(os.getenv("MOTION_SCORE", "5000"))


    return {
        "autostart_enabled": autostart_enabled,
        "TELEGRAM_TOKEN": telegram_token,
        "TELEGRAM_CHAT_ID": telegram_chat_id,
        "FASTMAIL_EMAIL": fastmail_email,
        "FASTMAIL_PASSWORD": fastmail_password,
        "FASTMAIL_RECIPIENT": fastmail_recipient,
        "cooldown":cooldown_seconds,
        "motion_score": motion_score,
        "dotenv_path": dotenv_path
    }

