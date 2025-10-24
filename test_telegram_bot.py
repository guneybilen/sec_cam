import asyncio
import datetime
from unittest.mock import MagicMock, AsyncMock

import pytest
from pytest import mark
from unittest.mock import patch

# --- test_stop_command_authorized_and_running ---
@mark.asyncio
async def test_stop_command_authorized_and_running(mocker):
    # Ensure telegram_bot uses the expected TELEGRAM_CHAT_ID
    mocker.patch('telegram_bot.TELEGRAM_CHAT_ID', '123456789')

    # Patch the detection event in the detection module (origin), not on telegram_bot
    mocker.patch('detection.detection_active_event.is_set', return_value=True)

    # Patch gui function where it is defined
    mock_stop = mocker.patch('gui.run_remote_stop_detection_on_main_thread')

    # Import handler after patches
    from telegram_bot import stop_command

    mock_update = MagicMock()
    mock_update.message.chat_id = 123456789
    mock_update.message.reply_text = AsyncMock()

    await stop_command(mock_update, MagicMock())

    mock_stop.assert_called_once()
    mock_update.message.reply_text.assert_awaited_with("Motion detector stopped remotely.")

# --- test_status_command_reports_running_state ---
@patch('notifications.get_motion_count', return_value=5)
@patch('gui.gui_exists', return_value=True)
def test_status_command_reports_running_state(mock_get_count, mock_gui_exists, mocker):
    # Ensure TELEGRAM_CHAT_ID matches
    mocker.patch('telegram_bot.TELEGRAM_CHAT_ID', '123456789')

    # Patch detection module objects (origin)
    mocker.patch('detection.detection_active_event.is_set', return_value=True)
    mock_thread = MagicMock()
    mock_thread.is_alive = MagicMock(return_value=True)
    mocker.patch('detection.detection_thread', mock_thread)

    # Telegram app/thread alive
    mocker.patch('telegram_bot.telegram_app', MagicMock(running=True))
    mocker.patch('telegram_bot.telegram_thread', MagicMock(is_alive=lambda: True))

    # last_motion_time set to 5 minutes ago on telegram_bot module
    five_min_ago = datetime.datetime.now() - datetime.timedelta(minutes=5)
    mocker.patch('telegram_bot.last_motion_time', five_min_ago)

    # Import handler after patches
    from telegram_bot import status_command

    mock_update = MagicMock()
    mock_update.message.chat_id = 123456789
    mock_update.message.reply_text = AsyncMock()

    asyncio.run(status_command(mock_update, MagicMock()))

    reply_text = mock_update.message.reply_text.call_args[0][0]

    assert "✅ GUI is active" in reply_text
    assert "✅ Detection thread running" in reply_text
    assert "✅ Telegram thread running" in reply_text
    assert "📈 Motion events count today: 5" in reply_text

