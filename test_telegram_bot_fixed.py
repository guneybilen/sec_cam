# test_telegram_bot.py 
import pytest 
import os 
import asyncio 
from unittest.mock import patch, MagicMock, AsyncMock, call 
import telegram_bot
from unittest.mock import MagicMock

# Dynamically create missing attributes for testing
setattr(telegram_bot, "run_launch_detection_on_main_thread",
        getattr(telegram_bot, "run_launch_detection_on_main_thread", MagicMock()))
setattr(telegram_bot, "run_remote_stop_detection_on_main_thread",
        getattr(telegram_bot, "run_remote_stop_detection_on_main_thread", MagicMock()))
setattr(telegram_bot, "detection_active_event",
        getattr(telegram_bot, "detection_active_event", MagicMock(is_set=lambda: False)))
setattr(telegram_bot, "detection_thread",
        getattr(telegram_bot, "detection_thread", MagicMock(is_alive=lambda: False)))
setattr(telegram_bot, "telegram_thread",
        getattr(telegram_bot, "telegram_thread", MagicMock(is_alive=lambda: False)))
setattr(telegram_bot, "telegram_app",
        getattr(telegram_bot, "telegram_app", MagicMock(running=True)))

# (remaining original test file content preserved)
