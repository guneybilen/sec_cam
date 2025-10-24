import threading
from unittest.mock import patch, MagicMock

@patch.dict('notifications.os.environ', {
    "TELEGRAM_TOKEN": "T_TOKEN",
    "TELEGRAM_CHAT_ID": "T_CHAT",
    "FROM_EMAIL": "sender@test.com",
    "APP_PASSWORD": "app_pwd",
    "FASTMAIL_RECIPIENT": "receiver@test.com"
})
@patch('notifications.load_dotenv', new=MagicMock())
@patch('notifications.threading.Thread')
@patch('notifications.os.path.getsize', return_value=0)
@patch('notifications.os.path.exists', return_value=True)
@patch('notifications.smtplib', new=MagicMock())
@patch('notifications.requests', new=MagicMock())
@patch('notifications.T', new=MagicMock())
def test_motion_count_threading(mock_thread, mock_getsize, mock_exists):
    from notifications import increment_motion_count, get_motion_count, reset_motion_count

    assert get_motion_count() == 0
    increment_motion_count()
    assert get_motion_count() == 1

    # Simulate 5 concurrent increments
    for _ in range(5):
        increment_motion_count()

    assert get_motion_count() == 6
    reset_motion_count()
    assert get_motion_count() == 0


