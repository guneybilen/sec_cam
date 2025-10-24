# This test suite ensures that config.py correctly loads environment variables and sets
# boolean/integer types.
# test_config.py 

import pytest 
import os 
from unittest.mock import patch, MagicMock 

# Assume config.py is importable 
# import config # Uncomment if running in the correct environment 
# Fixture to mock environment variables for config.py 

@pytest.fixture 
def mock_env(mocker): 
    # Mock os.getenv to control return values 
    mocker.patch.dict(os.environ, { 
        "AUTOSTART_ENABLED": "True", 
        "TELEGRAM_TOKEN": "mock_token_123", 
        "TELEGRAM_CHAT_ID": "1000", 
        "FASTMAIL_EMAIL": "test@fastmail.com", 
        "FASTMAIL_APP_PASSWORD": "app_password_xyz", 
        "FASTMAIL_RECIPIENT": "recipient@example.com", 
        "COOLDOWN_SECONDS": "60", 
        "MOTION_SCORE": "10000", 
    }, clear=True) 

# Mock load_dotenv and os.path.exists since we control os.environ directly 
@patch('config.load_dotenv', MagicMock()) 
@patch('config.os.path.exists', return_value=True) 
def test_load_config_success(mock_exists, mock_env): 
    # We redefine load_config locally or ensure we import the original function 
    from config import load_config 
    config_data = load_config() 
    # Assert types and values loaded correctly 
    assert config_data["autostart_enabled"] is True 
    assert config_data["TELEGRAM_TOKEN"] == "mock_token_123" 
    assert config_data["cooldown"] == 60 
    assert isinstance(config_data["cooldown"], int) 
    assert config_data["motion_score"] == 10000 
    assert isinstance(config_data["motion_score"], int) 
    assert config_data["FASTMAIL_EMAIL"] == "test@fastmail.com" 
    
@patch('config.load_dotenv', MagicMock()) 
@patch('config.os.path.exists', return_value=True) 
def test_load_config_defaults(mock_exists, mock_env, mocker): 
    # Clear optional environment variables to test defaults 
    mocker.patch.dict(os.environ, {"AUTOSTART_ENABLED": "False"}, clear=False) 
    for k in ("COOLDOWN_SECONDS", "MOTION_SCORE"): os.environ.pop(k, None)

    from config import load_config 
    config_data = load_config() 
    # Assert flags and defaults 
    assert config_data["autostart_enabled"] is False 
    assert config_data["cooldown"] == 30 
    assert config_data["motion_score"] == 5000 

@patch('config.os.getcwd', return_value='/app/test') 
@patch('config.load_dotenv') 
def test_dotenv_path_resolution(mock_load_dotenv, mock_getcwd, mocker): 
    # Test preference for local .env 
    mocker.patch('config.os.path.exists', side_effect=lambda p: p == '/app/test/.env') 
    from config import load_config 
    load_config() 
    # Check that it tried to load the local path 
    mock_load_dotenv.assert_called_with(dotenv_path='/app/test/.env') 
    # Test fallback path 
    mock_load_dotenv.reset_mock() 
    mocker.patch('config.os.path.exists', side_effect=lambda p: p == '/opt/motion-detector/.env') 
    from config import load_config 
    load_config() 
    # Check that it tried to load the fallback path 
    mock_load_dotenv.assert_called_with(dotenv_path='/opt/motion-detector/.env')
