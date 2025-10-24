# telegram_bot.py
import asyncio
import tracelog as T
# import traceback
from telegram.ext import Application, CommandHandler, ContextTypes
from notifications import send_telegram_alert
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder
import os


# Load environment variables
dotenv_path = os.path.join(os.getcwd(), ".env")
if not os.path.exists(dotenv_path):
    # Fallback for when the .desktop file's Path setting is ignored or failed
    dotenv_path = "/opt/motion-detector/.env"

load_dotenv(dotenv_path=dotenv_path)

# Telegram and Email Configuration (Read from .env)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Global Variables
telegram_app = None
telegram_bot_running = False
telegram_loop = None  # Reference to the asyncio event loop for clean shutdown
telegram_thread = None
last_motion_time = None
telegram_stop_event: asyncio.Event | None = None


def set_telegram_flag(value: bool):
    """Helper to set telegram_bot_running with debug output."""
    global telegram_bot_running
    telegram_bot_running = value
    T.info(f"[DEBUG] telegram_bot_running set to {value}")
    # traceback.print_stack(limit=4)  # show who called this
    

def is_telegram_running():
    return telegram_bot_running


from gui import enqueue_gui, update_telegram_status_label
enqueue_gui(update_telegram_status_label)

# --- Command Handlers ---
async def start_command(update, context):
    """Handles the /start_detector command from Telegram."""
    from detection import launch_detection, detection_active_event, detection_thread
    from gui import run_launch_detection_on_main_thread
    chat_id = update.message.chat_id

    # --- CRITICAL DEBUGGING ---
    # T.info(f"\n[❓] Received /start_detector from chat ID: {chat_id}")
    try:
        expected_id = int(TELEGRAM_CHAT_ID)
        # T.info(f"\n[❓] Expected authorized chat ID from .env: {expected_id}")
    except (TypeError, ValueError):
        expected_id = -1
        # T.error("\n[❌] TELEGRAM_CHAT_ID in .env is invalid or missing.")
    # --- END DEBUGGING ---

    # Basic authorization check
    if chat_id == expected_id:
        # T.info("\n[✅] Chat ID Authorized.")
        if detection_active_event.is_set() and detection_thread and detection_thread.is_alive():
            await update.message.reply_text("Motion detection is already running.")
            return

        # Use the thread-safe wrapper to queue the function call to the main Qt thread
        from gui import run_launch_detection_on_main_thread
        run_launch_detection_on_main_thread()

        await update.message.reply_text(
            "Motion detector started remotely. Watch for alerts!"
        )
    else:
        # print("\n[❌] Chat ID Unauthorized.")
        T.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
        await update.message.reply_text(
            f"Unauthorized access. Your chat ID ({chat_id}) is being logged. Expected ID: {expected_id}"
        )


async def stop_command(update, context):
    """Handles the /stop_detector command from Telegram."""
    from detection import shutdown_detection_pipeline, detection_active_event
    from gui import run_remote_stop_detection_on_main_thread
    chat_id = update.message.chat_id

    T.info(f"\n[❓] Received /stop_detector from chat ID: {chat_id}")
    try:
        expected_id = int(TELEGRAM_CHAT_ID)
    except (TypeError, ValueError):
        expected_id = -1

        # Basic authorization check
    if chat_id == expected_id:
        T.info("\n[✅] Chat ID Authorized.")
        if not detection_active_event.is_set():
            await update.message.reply_text("Motion detection is already idle.")
            return

        # Use the thread-safe wrapper to queue the function call to the main Qt thread
        from gui import run_remote_stop_detection_on_main_thread
        run_remote_stop_detection_on_main_thread()

        await update.message.reply_text(
            "Motion detector stopped remotely."
        )
    else:
        T.error("\n[❌] Chat ID Unauthorized.")
        T.warning(f"Unauthorized stop attempt from chat ID: {chat_id}")
        await update.message.reply_text(
            f"Unauthorized access. Your chat ID ({chat_id}) is being logged."
        )


async def status_command(update, context):
    """Handles the /status command to report system health."""
    from gui import gui_exists
    from notifications import get_motion_count
    from detection import detection_thread, detection_active_event
    from telegram_bot import telegram_thread, telegram_app

    chat_id = update.message.chat_id
    try:
        expected_id = int(TELEGRAM_CHAT_ID)
    except (TypeError, ValueError):
        expected_id = -1

    if chat_id != expected_id:
        T.warning(f"Unauthorized status request from chat ID: {chat_id}")
        await update.message.reply_text("Unauthorized access.")
        return

    status_lines = []

    status_lines.append("\n✅ GUI is active" if gui_exists() else "\n⚠️ GUI is not responding")

    if detection_active_event.is_set():
        if detection_thread and detection_thread.is_alive():
            status_lines.append("\n✅ Detection thread running")
        else:
            status_lines.append("\n❌ Detection thread missing")
    else:
        status_lines.append("\n🛑 Detection is idle")

    if telegram_app and getattr(telegram_app, "running", False):
        if telegram_thread and telegram_thread.is_alive():
            status_lines.append("\n✅ Telegram thread running")
        else:
            status_lines.append("\n❌ Telegram thread missing")
    else:
        status_lines.append("\n🛑 Telegram listener is stopped")

    count = get_motion_count()
    if last_motion_time:
        formatted_time = last_motion_time.strftime("%Y-%m-%d %H:%M:%S")
        import datetime
        seconds_ago = int((datetime.datetime.now() - last_motion_time).total_seconds())
        minutes, seconds = divmod(seconds_ago, 60)
        time_ago_str = f"\n{minutes}m {seconds}s ago" if minutes else f"\n{seconds}s ago"
        status_lines.append(f"\n📸 Last motion detected at: {formatted_time} ({time_ago_str})")
        status_lines.append(f"\n📈 Motion events count today: {count}")
    else:
        status_lines.append("\n📸 No motion detected yet")

    summary = "📊 System Status:\n" + "\n".join(status_lines)
    await update.message.reply_text(summary)


async def summary_command(update, context):
    """Handles the /summary command to report motion activity for today."""
    from notifications import get_motion_count
    count = get_motion_count()

    chat_id = update.message.chat_id
    try:
        expected_id = int(TELEGRAM_CHAT_ID)
    except (TypeError, ValueError):
        expected_id = -1

    if chat_id != expected_id:
        T.warning(f"Unauthorized summary request from chat ID: {chat_id}")
        await update.message.reply_text("Unauthorized access.")
        return

    import datetime
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    summary_lines = [f"\n📅 Motion Summary for {today}"]

    summary_lines.append(f"\n📈 Motion events today: {count}")

    if last_motion_time:
        formatted_time = last_motion_time.strftime("%Y-%m-%d %H:%M:%S")
        seconds_ago = int((datetime.datetime.now() - last_motion_time).total_seconds())
        minutes, seconds = divmod(seconds_ago, 60)
        time_ago_str = f"\n{minutes}m {seconds}s ago" if minutes else f"\n{seconds}s ago"
        summary_lines.append(f"\n📸 Last motion detected at: {formatted_time} ({time_ago_str})")
    else:
        summary_lines.append("\n📸 No motion detected yet")

    try:
        with open("motion_log.txt", "r") as f:
            lines = [line.strip() for line in f.readlines() if today in line and "Motion detected" in line]
        if lines:
            summary_lines.append("\n🧾 Recent motion events:")
            summary_lines.extend(lines[-5:])
        else:
            summary_lines.append("\n🧾 No logged motion events yet.")
    except Exception as e:
        T.error(f"Failed to read motion log for summary: {e}")
        summary_lines.append("\n⚠️ Could not read motion log.")

    await update.message.reply_text("\n".join(summary_lines))


# --- Telegram Bot Setup and Control ---
def _build_telegram_app():
    """Builds and configures the Telegram application."""
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start_detector", start_command))
    app.add_handler(CommandHandler("stop_detector", stop_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("summary", summary_command))
    return app


async def _shutdown_telegram_app(app):
    """Stops the Telegram application cleanly."""
    from gui import update_telegram_status_label

    if app and getattr(app, "running", False):
        T.info("[🛑] Stopping Telegram polling...")
        await app.updater.stop()  # ✅ Stop polling first
        T.info("[🛑] Stopping Telegram app...")
        await app.stop()  # ✅ Then stop the app
        T.info("[🛑] Shutting down Telegram app...")
        await app.shutdown()  # ✅ Finally shut down resources
        set_telegram_flag(False)
        update_telegram_status_label()
        T.info("[✅] Telegram bot shutdown complete.")
    T.info("[✅] Telegram application stopped.")


async def stop_telegram_bot():
    """Stop Telegram bot cleanly."""
    from gui import update_telegram_status_label
    global telegram_app

    if not telegram_app:
        return

    try:
        set_telegram_flag(False)
        await telegram_app.stop()         # ✅ stop run_polling
        await telegram_app.shutdown()
        enqueue_gui(update_telegram_status_label)
        T.info("[🛑] Telegram bot stopped cleanly.")
    except asyncio.CancelledError:
        T.info("Stopping Telegram Bot.")
    except Exception as e:
        T.error(f"[❌] Error while stopping Telegram bot: {e}")
    finally:
        telegram_app = None

async def start_telegram_listener_async():
    global telegram_bot_running, telegram_stop_event
    try:
        app = _build_telegram_app()

        set_telegram_flag(True)
        from gui import enqueue_gui, update_telegram_status_label
        enqueue_gui(update_telegram_status_label)

        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        telegram_stop_event = asyncio.Event()
        await telegram_stop_event.wait()

    except asyncio.CancelledError:
        # Normal shutdown path — don’t treat as error
        T.info("Telegram listener task cancelled (shutdown).")
    except Exception as e:
        # import traceback
        # T.error(f"Telegram bot crashed: {e}\n{traceback.format_exc()}")
        T.error(f"Telegram bot crashed: {e}")
    finally:
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            set_telegram_flag(False)
        except Exception as e:
            T.error(f"Telegram shutdown error: {e}")

        from gui import enqueue_gui, update_telegram_status_label
        enqueue_gui(update_telegram_status_label)


async def stop_telegram_listener_async():
    """Signal the Telegram bot to shut down gracefully."""
    global telegram_stop_event
    if telegram_stop_event is not None:
        set_telegram_flag(False)
        telegram_stop_event.set()
        T.info("[TELEGRAM] Shutdown signal sent.")

