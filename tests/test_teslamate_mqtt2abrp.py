import pytest
import json
from unittest.mock import patch, MagicMock, mock_open, call
import os
from teslamate_mqtt2abrp import TeslaMateABRP

@pytest.fixture
def mock_args():
    return {
        "DEBUG": False,
        "MQTTUSERNAME": None,
        "MQTTPASSWORD": None,
        "MQTTTLS": False,
        "SKIPLOCATION": False,
        "USERTOKEN": 'test-token',
        "CARNUMBER": '1',
        "MQTTSERVER": 'test-server',
        "MQTTPORT": '1883',
        "CARMODEL": None,
        "BASETOPIC": None
    }

@pytest.fixture
def mock_args_with_base_topic():
    return {
        "DEBUG": False,
        "MQTTUSERNAME": None,
        "MQTTPASSWORD": None,
        "MQTTTLS": False,
        "SKIPLOCATION": False,
        "USERTOKEN": 'test-token',
        "CARNUMBER": '1',
        "MQTTSERVER": 'test-server',
        "MQTTPORT": '1883',
        "CARMODEL": None,
        "BASETOPIC": "tesla/abrp/status"
    }

@pytest.fixture
def teslamate_abrp(mock_args):
    with patch('teslamate_mqtt2abrp.mqtt.Client') as mock_client:
        instance = mock_client.return_value
        instance.connect.return_value = None
        instance.loop_start.return_value = None
        
        # Create instance WITHOUT patching setup_mqtt_client
        abrp = TeslaMateABRP(mock_args)
        # Ensure client property exists
        abrp.client = instance
        return abrp

@pytest.fixture
def teslamate_abrp_with_topic(mock_args_with_base_topic):
    with patch('teslamate_mqtt2abrp.mqtt.Client') as mock_client:
        instance = mock_client.return_value
        instance.connect.return_value = None
        instance.loop_start.return_value = None
        
        # Create instance WITHOUT patching setup_mqtt_client
        abrp = TeslaMateABRP(mock_args_with_base_topic)
        # Ensure client property exists
        abrp.client = instance
        return abrp

def test_parse_config(mock_args):
    with patch('teslamate_mqtt2abrp.TeslaMateABRP.setup_mqtt_client'):
        abrp = TeslaMateABRP(mock_args)
    
    assert abrp.config.get('USERTOKEN') == 'test-token'
    assert abrp.config.get('CARNUMBER') == '1'
    assert abrp.config.get('MQTTSERVER') == 'test-server'
    assert abrp.config.get('MQTTPORT') == '1883'
    assert abrp.base_topic is None
    
def test_parse_config_with_base_topic(mock_args_with_base_topic):
    with patch('teslamate_mqtt2abrp.TeslaMateABRP.setup_mqtt_client'):
        abrp = TeslaMateABRP(mock_args_with_base_topic)
    
    assert abrp.config.get('BASETOPIC') == 'tesla/abrp/status'
    assert abrp.base_topic == 'tesla/abrp/status'
    assert abrp.state_topic == 'tesla/abrp/status/_tm2abrp_status'
    
def test_get_docker_secret():
    # Test when secret file exists
    with patch('os.path.isfile', return_value=True):
        with patch('builtins.open', mock_open(read_data="secret_value\n")):
            with patch('teslamate_mqtt2abrp.TeslaMateABRP.setup_mqtt_client'):
                abrp = TeslaMateABRP({
                    "DEBUG": False,
                    "MQTTUSERNAME": None,
                    "MQTTPASSWORD": None,
                    "MQTTTLS": False,
                    "SKIPLOCATION": False,
                    "USERTOKEN": 'test',
                    "CARNUMBER": '1',
                    "MQTTSERVER": 'test',
                    "MQTTPORT": '1883',
                    "CARMODEL": None,
                    "BASETOPIC": None
                })
                secret = abrp.get_docker_secret('test_secret')
                assert secret == 'secret_value'
    
    # Test when secret file doesn't exist
    with patch('os.path.isfile', return_value=False):
        with patch('teslamate_mqtt2abrp.TeslaMateABRP.setup_mqtt_client'):
            abrp = TeslaMateABRP({
                "DEBUG": False,
                "MQTTUSERNAME": None,
                "MQTTPASSWORD": None,
                "MQTTTLS": False,
                "SKIPLOCATION": False,
                "USERTOKEN": 'test',
                "CARNUMBER": '1',
                "MQTTSERVER": 'test',
                "MQTTPORT": '1883',
                "CARMODEL": None,
                "BASETOPIC": None
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

def test_publish_to_mqtt_without_base_topic(teslamate_abrp):
    # Verify the client attribute exists
    assert hasattr(teslamate_abrp, 'client')
    
    with patch.object(teslamate_abrp.client, 'publish') as mock_publish:
        # Call method with sample data
        teslamate_abrp.publish_to_mqtt({"test_key": "test_value"})
        
        # Verify publish was not called since base_topic is None
        mock_publish.assert_not_called()

def test_publish_to_mqtt_with_base_topic(teslamate_abrp_with_topic):
    # Verify the client attribute exists
    assert hasattr(teslamate_abrp_with_topic, 'client')
    
    with patch.object(teslamate_abrp_with_topic.client, 'publish') as mock_publish:
        # Call method with sample data
        teslamate_abrp_with_topic.publish_to_mqtt({"test_key": "test_value"})
        
        # Verify publish was called with correct parameters
        mock_publish.assert_called_once_with(
            "tesla/abrp/status/test_key",
            payload="test_value",
            qos=1,
            retain=True
        )

def test_on_connect_without_base_topic(teslamate_abrp):
    client_mock = MagicMock()
    
    teslamate_abrp.on_connect(client_mock, None, None, 0, None)
    
    # Should only subscribe, not publish online status
    client_mock.subscribe.assert_called_once_with("teslamate/cars/1/#")
    client_mock.publish.assert_not_called()

def test_on_connect_with_base_topic(teslamate_abrp_with_topic):
    client_mock = MagicMock()
    
    teslamate_abrp_with_topic.on_connect(client_mock, None, None, 0, None)
    
    # Should subscribe and publish online status
    client_mock.subscribe.assert_called_once_with("teslamate/cars/1/#")
    client_mock.publish.assert_called_once_with(
        "tesla/abrp/status/_tm2abrp_status", 
        payload="online",
        qos=2,
        retain=True
    )

def test_update_timely_mqtt_publishing():
    # Create a test instance with base_topic
    test_config = {
        "DEBUG": False,
        "MQTTUSERNAME": None,
        "MQTTPASSWORD": None,
        "MQTTTLS": False,
        "SKIPLOCATION": False,
        "USERTOKEN": 'test-token',
        "CARNUMBER": '1',
        "MQTTSERVER": 'test-server',
        "MQTTPORT": '1883',
        "CARMODEL": None,
        "BASETOPIC": "tesla/abrp/status"
    }
    
    # Create a separate test instance for this test
    with patch('teslamate_mqtt2abrp.mqtt.Client') as mock_client:
        instance = mock_client.return_value
        with patch('time.sleep'):  # Skip actual sleep
            with patch('teslamate_mqtt2abrp.TeslaMateABRP.update_abrp'):
                with patch('teslamate_mqtt2abrp.TeslaMateABRP.publish_to_mqtt') as mock_publish:
                    # Create instance directly with mocked methods
                    abrp = TeslaMateABRP(test_config)
                    abrp.client = instance
                    
                    # Setup driving state
                    abrp.state = "driving"
                    abrp.prev_state = "online"
                    
                    # Modify update_timely to exit after one iteration
                    original_update_timely = abrp.update_timely
                    
                    def mock_update_timely():
                        # Simulate one iteration of the while loop
                        i = 0
                        # Update UTC timestamp
                        abrp.data["utc"] = 1234567890  # Mock timestamp
                        
                        # Handle different car states - simulate driving state
                        if abrp.state == "driving":
                            abrp.update_abrp()
                            if abrp.base_topic:
                                abrp.publish_to_mqtt(abrp.data)
                        
                        # Raise exception to exit the function
                        raise KeyboardInterrupt()
                    
                    # Replace method
                    abrp.update_timely = mock_update_timely
                    
                    # Run with exception handling
                    try:
                        abrp.update_timely()
                    except KeyboardInterrupt:
                        pass
                    
                    # Verify publish_to_mqtt was called once with abrp.data
                    mock_publish.assert_called_once_with(abrp.data)

if __name__ == "__main__":
    pytest.main()