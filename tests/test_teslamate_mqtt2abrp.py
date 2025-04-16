import pytest
import json
from unittest.mock import patch, MagicMock, mock_open
import os
from teslamate_mqtt2abrp import TeslaMateABRP

@pytest.fixture
def mock_args():
    return {
        '-d': False,
        '-l': False,
        '-p': False,
        '-s': False,
        '-x': False,
        'USER_TOKEN': 'test-token',
        'CAR_NUMBER': '1',
        'MQTT_SERVER': 'test-server',
        'MQTT_PORT': '1883',
        'MQTT_USERNAME': None,
        'MQTT_PASSWORD': None,
        '--model': None,
        '--status_topic': None
    }

@pytest.fixture
def teslamate_abrp(mock_args):
    with patch('teslamate_mqtt2abrp.mqtt.Client') as mock_client:
        instance = mock_client.return_value
        instance.connect.return_value = None
        instance.loop_start.return_value = None
        with patch('teslamate_mqtt2abrp.TeslaMateABRP.setup_mqtt_client'):
            return TeslaMateABRP(mock_args)

def test_parse_config(mock_args):
    with patch('teslamate_mqtt2abrp.TeslaMateABRP.setup_mqtt_client'):
        abrp = TeslaMateABRP(mock_args)
    
    assert abrp.config.get('USERTOKEN') == 'test-token'
    assert abrp.config.get('CARNUMBER') == '1'
    assert abrp.config.get('MQTTSERVER') == 'test-server'
    assert abrp.config.get('MQTTPORT') == 1883
    
def test_get_docker_secret():
    # Test when secret file exists
    with patch('os.path.isfile', return_value=True):
        with patch('builtins.open', mock_open(read_data="secret_value\n")):
            with patch('teslamate_mqtt2abrp.TeslaMateABRP.setup_mqtt_client'):
                abrp = TeslaMateABRP({
                    '-d': False, '-l': False, '-p': False, '-s': False, '-x': False,
                    'USER_TOKEN': 'test', 'CAR_NUMBER': '1', 'MQTT_SERVER': 'test',
                    'MQTT_PORT': '1883', 'MQTT_USERNAME': None, 'MQTT_PASSWORD': None,
                    '--model': None, '--status_topic': None
                })
                secret = abrp.get_docker_secret('test_secret')
                assert secret == 'secret_value'
    
    # Test when secret file doesn't exist
    with patch('os.path.isfile', return_value=False):
        with patch('teslamate_mqtt2abrp.TeslaMateABRP.setup_mqtt_client'):
            abrp = TeslaMateABRP({
                '-d': False, '-l': False, '-p': False, '-s': False, '-x': False,
                'USER_TOKEN': 'test', 'CAR_NUMBER': '1', 'MQTT_SERVER': 'test',
                'MQTT_PORT': '1883', 'MQTT_USERNAME': None, 'MQTT_PASSWORD': None,
                '--model': None, '--status_topic': None
            })
            secret = abrp.get_docker_secret('test_secret')
            assert secret is None

def test_process_message(teslamate_abrp):
    # Test normal message processing
    teslamate_abrp.process_message("model", "3")
    assert teslamate_abrp.data["model"] == "3"
    
    # Test numeric conversion
    teslamate_abrp.process_message("speed", "65")
    assert teslamate_abrp.data["speed"] == 65
    
    # Test invalid numeric values
    teslamate_abrp.process_message("speed", "invalid")
    assert teslamate_abrp.data["speed"] == 65  # Should not change
    
    # Test location skipping
    teslamate_abrp.config["SKIPLOCATION"] = True
    teslamate_abrp.process_message("latitude", "37.7749")
    assert teslamate_abrp.data["lat"] == 0  # Should not change when skipping location
    
    teslamate_abrp.config["SKIPLOCATION"] = False
    teslamate_abrp.process_message("latitude", "37.7749")
    assert teslamate_abrp.data["lat"] == 37.7749
    
def test_handle_state_change(teslamate_abrp):
    # Test driving state
    teslamate_abrp.handle_state_change("driving")
    assert teslamate_abrp.data["is_parked"] == False
    assert teslamate_abrp.data["is_charging"] == False
    assert teslamate_abrp.data["is_dcfc"] == False
    
    # Test charging state
    teslamate_abrp.handle_state_change("charging")
    assert teslamate_abrp.data["is_parked"] == True
    assert teslamate_abrp.data["is_charging"] == True
    assert teslamate_abrp.data["is_dcfc"] == False
    
    # Test supercharging state
    teslamate_abrp.handle_state_change("supercharging")
    assert teslamate_abrp.data["is_parked"] == True
    assert teslamate_abrp.data["is_charging"] == True
    assert teslamate_abrp.data["is_dcfc"] == True
    
    # Test parked state
    teslamate_abrp.handle_state_change("online")
    assert teslamate_abrp.data["is_parked"] == True
    assert teslamate_abrp.data["is_charging"] == False
    assert teslamate_abrp.data["is_dcfc"] == False

def test_find_car_model(teslamate_abrp):
    # Test Model 3 detection
    teslamate_abrp.data["model"] = "3"
    teslamate_abrp.data["trim_badging"] = "74D"
    
    with patch('time.sleep'):  # Mock sleep to avoid waiting
        teslamate_abrp.find_car_model()
    
    assert teslamate_abrp.data["car_model"] == "3long_awd"
    
    # Test Model Y detection
    teslamate_abrp.data["model"] = "Y"
    teslamate_abrp.data["trim_badging"] = "P74D"
    
    with patch('time.sleep'):
        teslamate_abrp.find_car_model()
    
    assert teslamate_abrp.data["car_model"] == "tesla:my:19:bt37:perf"
    
    # Test Model S detection
    teslamate_abrp.data["model"] = "S"
    teslamate_abrp.data["trim_badging"] = "100d"
    
    with patch('time.sleep'):
        teslamate_abrp.find_car_model()
    
    assert teslamate_abrp.data["car_model"] == "s100d"

def test_update_abrp(teslamate_abrp):
    with patch('requests.post') as mock_post:
        # Setup mock response
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "ok"}
        mock_post.return_value = mock_response
        
        # Call update_abrp
        teslamate_abrp.update_abrp()
        
        # Verify the API call
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "https://api.iternio.com/1/tlm/send" in args[0]
        assert "token=test-token" in args[0]
        assert kwargs["headers"]["Authorization"].startswith("APIKEY ")
        assert "tlm" in kwargs["json"]
        assert kwargs["timeout"] == 10
        
        # Test error handling
        mock_response.json.return_value = {"status": "error"}
        teslamate_abrp.update_abrp()
        
        # Test exception handling
        mock_post.side_effect = Exception("Test exception")
        teslamate_abrp.update_abrp()

if __name__ == "__main__":
    pytest.main()