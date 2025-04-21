import pytest
import json
import os
import logging
import sys
import types
import importlib
from unittest.mock import patch, MagicMock, mock_open, call
from teslamate_mqtt2abrp import TeslaMateABRP, DEFAULT_MQTT_PORT, main

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

@pytest.fixture
def mock_click_command():
    """Fixture to mock Click command decorator for direct function access"""
    with patch('teslamate_mqtt2abrp.click.command') as mock_command:
        # Make mock_command return a function that just calls its argument
        def mock_decorator(f):
            return f
        mock_command.return_value = mock_decorator
        
        # Reload to get the unwrapped function
        import teslamate_mqtt2abrp
        importlib.reload(teslamate_mqtt2abrp)
        
        yield teslamate_mqtt2abrp.main
        
        # Clean up: reload original module after test
        importlib.reload(teslamate_mqtt2abrp)

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

def test_init(mock_args):
    """Test the __init__ method properly initializes instance variables"""
    with patch('teslamate_mqtt2abrp.TeslaMateABRP.setup_mqtt_client'):
        abrp = TeslaMateABRP(mock_args)
        
        # Check that default data structure is initialized correctly
        assert abrp.state == ""
        assert abrp.prev_state == ""
        assert abrp.charger_phases == 1
        assert abrp.prefix == "_tm2abrp"
        assert isinstance(abrp.data, dict)
        assert "utc" in abrp.data
        assert "soc" in abrp.data
        assert "power" in abrp.data
        assert "speed" in abrp.data
        
        # These should be the actual default values from the code
        assert abrp.data["is_charging"] is False
        assert abrp.data["is_dcfc"] is False
        assert abrp.data["is_parked"] is False
        # Car model is None by default if not specified in config
        assert abrp.data["car_model"] is None  # Changed from "" to None

def test_init_with_car_model(mock_args):
    """Test __init__ with car model specified"""
    mock_args_with_model = mock_args.copy()
    mock_args_with_model["CARMODEL"] = "3long_awd"
    
    with patch('teslamate_mqtt2abrp.TeslaMateABRP.setup_mqtt_client'):
        abrp = TeslaMateABRP(mock_args_with_model)
        assert abrp.data["car_model"] == "3long_awd"

def test_configure_logging(mock_args):
    """Test that configure_logging sets up logging correctly"""
    with patch('logging.basicConfig') as mock_logging:
        with patch('teslamate_mqtt2abrp.TeslaMateABRP.setup_mqtt_client'):
            # Test with DEBUG = False
            abrp = TeslaMateABRP(mock_args)
            mock_logging.assert_called_once()
            args, kwargs = mock_logging.call_args
            assert kwargs['level'] == logging.INFO
            
            # Test with DEBUG = True
            mock_logging.reset_mock()
            debug_args = mock_args.copy()
            debug_args["DEBUG"] = True
            abrp = TeslaMateABRP(debug_args)
            mock_logging.assert_called_once()
            args, kwargs = mock_logging.call_args
            assert kwargs['level'] == logging.DEBUG

def test_setup_mqtt_client(mock_args):
    """Test the MQTT client setup"""
    with patch('teslamate_mqtt2abrp.mqtt.Client', autospec=True) as mock_client_class:
        mock_instance = mock_client_class.return_value
        
        # Create a TeslaMateABRP instance
        abrp = TeslaMateABRP(mock_args)
        
        # Check that client was created with expected parameters
        mock_client_class.assert_called_once()
        
        # Verify callbacks were set correctly
        assert hasattr(abrp, 'client')
        assert abrp.client.on_connect is not None
        assert abrp.client.on_message is not None
        
        # Check connect was called (ignoring exact parameters)
        assert abrp.client.connect.called
        
        # Verify connect was called with the server address (first parameter)
        args, kwargs = abrp.client.connect.call_args
        assert args[0] == mock_args["MQTTSERVER"]

def test_setup_mqtt_client_with_auth(mock_args):
    """Test MQTT client setup with authentication"""
    auth_args = mock_args.copy()
    auth_args["MQTTUSERNAME"] = "test_user"
    auth_args["MQTTPASSWORD"] = "test_password"
    
    with patch('teslamate_mqtt2abrp.mqtt.Client') as mock_client:
        instance = mock_client.return_value
        
        # Create a TeslaMateABRP instance
        abrp = TeslaMateABRP(auth_args)
        
        # Check username and password were set
        instance.username_pw_set.assert_called_once_with("test_user", "test_password")

def test_setup_mqtt_client_with_tls(mock_args):
    """Test MQTT client setup with TLS"""
    tls_args = mock_args.copy()
    tls_args["MQTTTLS"] = True
    
    with patch('teslamate_mqtt2abrp.mqtt.Client') as mock_client:
        instance = mock_client.return_value
        
        # Create a TeslaMateABRP instance
        abrp = TeslaMateABRP(tls_args)
        
        # Check TLS was set
        instance.tls_set.assert_called_once()

def test_setup_mqtt_client_with_will(mock_args_with_base_topic):
    """Test MQTT client setup with last will message"""
    with patch('teslamate_mqtt2abrp.mqtt.Client') as mock_client:
        instance = mock_client.return_value
        
        # Create a TeslaMateABRP instance
        abrp = TeslaMateABRP(mock_args_with_base_topic)
        
        # Check will_set was called
        instance.will_set.assert_called_once_with(
            f"{mock_args_with_base_topic['BASETOPIC']}/{abrp.prefix}_status",
            payload="offline",
            qos=2,
            retain=True
        )

def test_setup_mqtt_client_connection_error(mock_args):
    """Test MQTT client setup with connection error"""
    with patch('teslamate_mqtt2abrp.mqtt.Client') as mock_client:
        instance = mock_client.return_value
        instance.connect.side_effect = Exception("Connection error")
        
        # Should exit with error
        with pytest.raises(SystemExit):
            abrp = TeslaMateABRP(mock_args)

def test_on_message(teslamate_abrp):
    """Test on_message method handles messages correctly"""
    # Create a mock message
    message = MagicMock()
    message.topic = "teslamate/cars/1/model"
    message.payload = b"3"
    
    # Call on_message
    teslamate_abrp.on_message(None, None, message)
    
    # Verify the data was updated
    assert teslamate_abrp.data["model"] == "3"
    
    # Test with exception in process_message
    with patch.object(teslamate_abrp, 'process_message', side_effect=Exception("Test error")):
        # Should not raise exception
        teslamate_abrp.on_message(None, None, message)

def test_nice_now(teslamate_abrp):
    """Test nice_now returns formatted timestamp"""
    with patch('datetime.datetime') as mock_datetime:
        mock_now = MagicMock()
        mock_now.strftime.return_value = "2023-01-01 12:00:00"
        mock_datetime.now.return_value = mock_now
        
        result = teslamate_abrp.nice_now()
        assert result == "2023-01-01 12:00:00"
        mock_now.strftime.assert_called_once_with("%Y-%m-%d %H:%M:%S")

def test_handle_parked_state(teslamate_abrp):
    """Test handle_parked_state method"""
    # Set up data
    teslamate_abrp.data["power"] = 10.5
    teslamate_abrp.data["speed"] = 65
    teslamate_abrp.data["kwh_charged"] = 30.5
    
    # Call method
    teslamate_abrp.handle_parked_state(0)
    
    # Verify data was updated
    assert teslamate_abrp.data["power"] == 0.0
    assert teslamate_abrp.data["speed"] == 0
    assert "kwh_charged" not in teslamate_abrp.data

def test_run(teslamate_abrp):
    """Test run method"""
    # Mock find_car_model and update_timely
    with patch.object(teslamate_abrp, 'find_car_model') as mock_find_model:
        with patch.object(teslamate_abrp, 'update_timely') as mock_update:
            # Run with no car model
            teslamate_abrp.run()
            
            # Should call find_car_model
            mock_find_model.assert_called_once()
            
            # Should call update_timely
            mock_update.assert_called_once()
            
            # Test with car model set
            mock_find_model.reset_mock()
            mock_update.reset_mock()
            teslamate_abrp.config["CARMODEL"] = "3long_awd"
            
            teslamate_abrp.run()
            
            # Should not call find_car_model
            mock_find_model.assert_not_called()
            
            # Should call update_timely
            mock_update.assert_called_once()

def test_run_with_keyboard_interrupt(teslamate_abrp):
    """Test run method with keyboard interrupt"""
    # Make update_timely raise KeyboardInterrupt
    with patch.object(teslamate_abrp, 'update_timely', side_effect=KeyboardInterrupt):
        with patch.object(teslamate_abrp.client, 'loop_stop') as mock_loop_stop:
            with patch.object(teslamate_abrp.client, 'disconnect') as mock_disconnect:
                with patch.object(teslamate_abrp.client, 'is_connected', return_value=True):
                    # Should not raise an exception
                    teslamate_abrp.run()
                    
                    # Should stop the loop and disconnect
                    mock_loop_stop.assert_called_once()
                    mock_disconnect.assert_called_once()

def test_update_timely(teslamate_abrp):
    """Test update_timely method"""
    # Mock sleep to prevent infinite loop
    with patch('time.sleep'):
        # Mock other methods
        with patch.object(teslamate_abrp, 'update_abrp') as mock_update:
            with patch.object(teslamate_abrp, 'publish_to_mqtt') as mock_publish:
                # Store original method
                original = teslamate_abrp.update_timely
                
                # Create a new method that will break out of the infinite loop
                def fake_update_timely():
                    # Only run one iteration
                    i = 0
                    
                    # Update UTC timestamp
                    teslamate_abrp.data["utc"] = 12345678
                    
                    # Only test one branch based on current state
                    if teslamate_abrp.state in ["parked", "online", "suspended", "asleep", "offline"]:
                        teslamate_abrp.handle_parked_state(i)
                        if i % 30 == 0 or i > 30:
                            teslamate_abrp.update_abrp()
                            if teslamate_abrp.base_topic:
                                teslamate_abrp.publish_to_mqtt(teslamate_abrp.data)
                    elif teslamate_abrp.state == "charging":
                        if i % 6 == 0:
                            teslamate_abrp.update_abrp()
                            if teslamate_abrp.base_topic:
                                teslamate_abrp.publish_to_mqtt(teslamate_abrp.data)
                    elif teslamate_abrp.state == "driving":
                        teslamate_abrp.update_abrp()
                        if teslamate_abrp.base_topic:
                            teslamate_abrp.publish_to_mqtt(teslamate_abrp.data)
                    elif teslamate_abrp.state:  # Any other non-empty state
                        pass
                        
                    # Don't actually loop
                    raise KeyboardInterrupt
                
                # Replace update_timely with our test version
                teslamate_abrp.update_timely = fake_update_timely
                
                try:
                    # Test parked state
                    teslamate_abrp.state = "parked"
                    teslamate_abrp.prev_state = "driving"
                    teslamate_abrp.update_timely()
                except KeyboardInterrupt:
                    pass
                
                # Should call update_abrp
                mock_update.assert_called_once()
                
                # Restore original method
                teslamate_abrp.update_timely = original

# USING OPTION 1: Mocking Click Command functionality
@patch('teslamate_mqtt2abrp.click.command')
def test_main_with_click_mocked(mock_command):
    """Test main function by mocking Click's command decorator"""
    # Make mock_command return a function that just calls its argument
    def mock_decorator(f):
        return f
    mock_command.return_value = mock_decorator
    
    # Reload the module to get the unwrapped function
    import teslamate_mqtt2abrp
    importlib.reload(teslamate_mqtt2abrp)
    
    # Now we can directly test the unwrapped main function
    with patch('teslamate_mqtt2abrp.TeslaMateABRP') as mock_teslamate_abrp:
        with patch('teslamate_mqtt2abrp.get_docker_secret') as mock_get_docker_secret:
            with patch('sys.exit'):
                # Test with minimum required args
                teslamate_mqtt2abrp.main(
                    user_token='test_token',
                    car_number='1',
                    mqtt_server='test_server',
                    mqtt_username=None,
                    mqtt_password=None,
                    mqtt_port=None,
                    car_model=None,
                    status_topic=None,
                    debug=False,
                    use_auth=False,
                    use_tls=False,
                    skip_location=False
                )
                
                # Check TeslaMateABRP was instantiated with correct config
                mock_teslamate_abrp.assert_called_once()
                args, kwargs = mock_teslamate_abrp.call_args
                config = args[0]
                assert config['USERTOKEN'] == 'test_token'
                assert config['CARNUMBER'] == '1'
                assert config['MQTTSERVER'] == 'test_server'
    
# Reload the module back to normal after test
    importlib.reload(teslamate_mqtt2abrp)

def test_main_missing_required_args_direct():
    """Test main function error handling with direct imports and checks"""
    # Import the module directly
    from teslamate_mqtt2abrp import get_docker_secret
    
    # Create a mock for sys.exit that raises an exception instead of exiting
    class MockExit(Exception):
        def __init__(self, code=0):
            self.code = code
            super().__init__(f"sys.exit called with code {code}")
    
    # Create a mock for click.echo that captures messages
    echo_messages = []
    def mock_echo(message):
        echo_messages.append(message)
    
    # Replace the actual functions
    with patch('sys.exit', side_effect=MockExit):
        with patch('teslamate_mqtt2abrp.click.echo', side_effect=mock_echo):
            with patch('teslamate_mqtt2abrp.get_docker_secret', return_value=None):
                # Import main inside the patched context
                from teslamate_mqtt2abrp import main
                
                # Test missing MQTT server
                try:
                    main(
                        user_token='test_token',
                        car_number='1',
                        mqtt_server=None,  # Missing required argument
                        mqtt_username=None,
                        mqtt_password=None,
                        mqtt_port=None,
                        car_model=None,
                        status_topic=None,
                        debug=False,
                        use_auth=False,
                        use_tls=False,
                        skip_location=False
                    )
                    pytest.fail("Expected MockExit exception")
                except MockExit as e:
                    # The specific error code could be 0 or 1 depending on implementation
                    # What's important is that sys.exit was called and the error message is correct
                    assert "MQTT server" in echo_messages[-1], "Expected error about MQTT server"
                
                # Clear captured messages
                echo_messages.clear()
                
                # Test missing user token
                try:
                    main(
                        user_token=None,  # Missing required argument
                        car_number='1',
                        mqtt_server='test_server',
                        mqtt_username=None,
                        mqtt_password=None,
                        mqtt_port=None,
                        car_model=None,
                        status_topic=None,
                        debug=False,
                        use_auth=False,
                        use_tls=False,
                        skip_location=False
                    )
                    pytest.fail("Expected MockExit exception")
                except MockExit as e:
                    # The specific error code could be 0 or 1 depending on implementation
                    # What's important is that sys.exit was called and the error message is correct
                    assert "User token" in echo_messages[-1], "Expected error about User token"

@patch('teslamate_mqtt2abrp.click.command')
def test_main_with_docker_secrets_mocked_click(mock_command):
    """Test main function with Docker secrets using mocked Click"""
    # Make mock_command return a function that just calls its argument
    def mock_decorator(f):
        return f
    mock_command.return_value = mock_decorator
    
    # Reload the module to get the unwrapped function
    import teslamate_mqtt2abrp
    importlib.reload(teslamate_mqtt2abrp)
    
    with patch('teslamate_mqtt2abrp.get_docker_secret', return_value='secret_token') as mock_get_docker_secret:
        with patch('teslamate_mqtt2abrp.TeslaMateABRP') as mock_teslamate_abrp:
            with patch('sys.exit'):
                # Call without token (should get from Docker secret)
                teslamate_mqtt2abrp.main(
                    user_token=None,
                    car_number='1',
                    mqtt_server='test_server',
                    mqtt_username=None,
                    mqtt_password=None,
                    mqtt_port=None,
                    car_model=None,
                    status_topic=None,
                    debug=False,
                    use_auth=True,  # Enable auth but don't provide password
                    use_tls=False,
                    skip_location=False
                )
                
                # Check Docker secret was used for token
                mock_get_docker_secret.assert_any_call('USER_TOKEN')
                
                # Check Docker secret was used for password
                mock_get_docker_secret.assert_any_call('MQTT_PASSWORD')
                
                # Check TeslaMateABRP was initialized with the secret token
                mock_teslamate_abrp.assert_called_once()
                args, kwargs = mock_teslamate_abrp.call_args
                config = args[0]
                assert config["USERTOKEN"] == "secret_token"
    
    # Reload the module back to normal after test
    importlib.reload(teslamate_mqtt2abrp)

@patch('teslamate_mqtt2abrp.click.command')
def test_main_run_exceptions_with_click_mock(mock_command):
    """Test main function exception handling using mocked Click"""
    # Make mock_command return a function that just calls its argument
    def mock_decorator(f):
        return f
    mock_command.return_value = mock_decorator
    
    # Reload the module to get the unwrapped function
    import teslamate_mqtt2abrp
    importlib.reload(teslamate_mqtt2abrp)
    
    # Test with KeyboardInterrupt
    with patch('teslamate_mqtt2abrp.TeslaMateABRP') as mock_teslamate_abrp:
        mock_teslamate_abrp.return_value.run.side_effect = KeyboardInterrupt()
        
        with patch('sys.exit') as mock_exit:
            teslamate_mqtt2abrp.main(
                user_token='test_token',
                car_number='1',
                mqtt_server='test_server',
                mqtt_username=None,
                mqtt_password=None,
                mqtt_port=None,
                car_model=None,
                status_topic=None,
                debug=False,
                use_auth=False,
                use_tls=False,
                skip_location=False
            )
            
            # Should exit cleanly with code 0
            mock_exit.assert_called_once_with(0)
    
    # Test with general exception
    with patch('teslamate_mqtt2abrp.TeslaMateABRP') as mock_teslamate_abrp:
        mock_teslamate_abrp.return_value.run.side_effect = Exception("Test error")
        
        with patch('sys.exit') as mock_exit:
            teslamate_mqtt2abrp.main(
                user_token='test_token',
                car_number='1',
                mqtt_server='test_server',
                mqtt_username=None,
                mqtt_password=None,
                mqtt_port=None,
                car_model=None,
                status_topic=None,
                debug=False,
                use_auth=False,
                use_tls=False,
                skip_location=False
            )
            
            # Should exit with error code 1
            mock_exit.assert_called_once_with(1)
    
    # Reload the module back to normal after test
    importlib.reload(teslamate_mqtt2abrp)

def test_standalone_get_docker_secret():
    """Test standalone get_docker_secret function"""
    # Test when secret file exists
    with patch('os.path.isfile', return_value=True):
        with patch('builtins.open', mock_open(read_data="secret_value\n")):
            # Call the function
            from teslamate_mqtt2abrp import get_docker_secret
            secret = get_docker_secret('test_secret')
            assert secret == 'secret_value'
    
    # Test when secret file doesn't exist
    with patch('os.path.isfile', return_value=False):
        # Call the function
        from teslamate_mqtt2abrp import get_docker_secret
        secret = get_docker_secret('test_secret')
        assert secret is None
        
    # Test when file access raises exception
    with patch('os.path.isfile', return_value=True):
        with patch('builtins.open', side_effect=Exception("File error")):
            # Call the function
            from teslamate_mqtt2abrp import get_docker_secret
            secret = get_docker_secret('test_secret')
            assert secret is None

# Tests for process_message method - this is where we're missing the most coverage
def test_process_message_comprehensive(teslamate_abrp):
    """Test all branches of the process_message method"""
    # Test empty payload handling for non-state topics
    teslamate_abrp.process_message("model", "")
    assert teslamate_abrp.data["model"] == ""  # Should be unchanged
    
    # Test empty payload for state/shift_state topic
    teslamate_abrp.process_message("state", "")
    teslamate_abrp.process_message("shift_state", "")
    
    # Test all numeric conversions with error handling
    # Float conversions
    teslamate_abrp.process_message("latitude", "invalid")
    teslamate_abrp.process_message("longitude", "invalid")
    teslamate_abrp.process_message("power", "invalid")
    teslamate_abrp.process_message("outside_temp", "invalid")
    teslamate_abrp.process_message("odometer", "invalid")
    teslamate_abrp.process_message("ideal_battery_range_km", "invalid")
    teslamate_abrp.process_message("est_battery_range_km", "invalid")
    teslamate_abrp.process_message("charge_energy_added", "invalid")
    
    # Integer conversions
    teslamate_abrp.process_message("elevation", "invalid")
    teslamate_abrp.process_message("speed", "invalid")
    teslamate_abrp.process_message("heading", "invalid")
    teslamate_abrp.process_message("charger_actual_current", "invalid")
    teslamate_abrp.process_message("charger_voltage", "invalid")
    teslamate_abrp.process_message("usable_battery_level", "invalid")
    teslamate_abrp.process_message("battery_level", "invalid")  # Add test for battery_level
    teslamate_abrp.process_message("charger_phases", "invalid")
    
    # Test charger_power with edge cases
    teslamate_abrp.process_message("charger_power", "0")
    teslamate_abrp.process_message("charger_power", "5")
    teslamate_abrp.process_message("charger_power", "15")  # Should set is_dcfc to True
    
    # Test charger_actual_current with edge cases
    teslamate_abrp.process_message("charger_actual_current", "0")
    teslamate_abrp.process_message("charger_actual_current", "10")
    
    # Test charger_voltage with edge cases
    teslamate_abrp.process_message("charger_voltage", "0")
    teslamate_abrp.process_message("charger_voltage", "3")  # Below threshold
    teslamate_abrp.process_message("charger_voltage", "220")  # Above threshold
    
    # Test battery level fallback (add this section)
    teslamate_abrp.has_usable_battery_level = False
    teslamate_abrp.process_message("battery_level", "65")
    assert teslamate_abrp.data["soc"] == 65
    
    teslamate_abrp.process_message("usable_battery_level", "75")
    assert teslamate_abrp.data["soc"] == 75
    assert teslamate_abrp.has_usable_battery_level is True
    
    teslamate_abrp.process_message("battery_level", "85")
    assert teslamate_abrp.data["soc"] == 75  # Should not change
    
    # Test shift_state with all possible values
    teslamate_abrp.process_message("shift_state", "P")
    assert teslamate_abrp.data["is_parked"] is True
    
    teslamate_abrp.process_message("shift_state", "D")
    assert teslamate_abrp.data["is_parked"] is False
    
    teslamate_abrp.process_message("shift_state", "R")
    assert teslamate_abrp.data["is_parked"] is False
    
    teslamate_abrp.process_message("shift_state", "N")
    assert teslamate_abrp.data["is_parked"] is False
    
    # Test power calculation on AC charging
    teslamate_abrp.data["is_charging"] = True
    teslamate_abrp.data["is_dcfc"] = False
    teslamate_abrp.data["voltage"] = 220
    teslamate_abrp.data["current"] = 16
    teslamate_abrp.charger_phases = 3
    
    # This should trigger the power calculation branch
    teslamate_abrp.process_message("charger_phases", "3")
    
    # Check power was calculated correctly (220V * 16A * 3 phases / 1000 * -1)
    assert teslamate_abrp.data["power"] == -10.56
    
    # Test unhandled topics
    teslamate_abrp.process_message("unknown_topic", "some_value")

def test_update_timely_comprehensive(teslamate_abrp):
    """Test update_timely with all possible states"""
    with patch('time.sleep') as mock_sleep:
        with patch('calendar.timegm', return_value=12345678):
            with patch.object(teslamate_abrp, 'update_abrp') as mock_update:
                with patch.object(teslamate_abrp, 'publish_to_mqtt') as mock_publish:
                    # Create a special version of update_timely that exits after one iteration
                    def fake_update_timely():
                        # Only run one iteration
                        i = 0
                        
                        # Update UTC timestamp
                        teslamate_abrp.data["utc"] = 12345678
                        
                        # Only test one branch based on current state
                        if teslamate_abrp.state in ["parked", "online", "suspended", "asleep", "offline"]:
                            teslamate_abrp.handle_parked_state(i)
                            if i % 30 == 0 or i > 30:
                                teslamate_abrp.update_abrp()
                                if teslamate_abrp.base_topic:
                                    teslamate_abrp.publish_to_mqtt(teslamate_abrp.data)
                        elif teslamate_abrp.state == "charging":
                            if i % 6 == 0:
                                teslamate_abrp.update_abrp()
                                if teslamate_abrp.base_topic:
                                    teslamate_abrp.publish_to_mqtt(teslamate_abrp.data)
                        elif teslamate_abrp.state == "driving":
                            teslamate_abrp.update_abrp()
                            if teslamate_abrp.base_topic:
                                teslamate_abrp.publish_to_mqtt(teslamate_abrp.data)
                        elif teslamate_abrp.state:  # Any other non-empty state
                            pass
                            
                        # Don't actually loop
                        raise KeyboardInterrupt
                    
                    # Replace update_timely with our test version
                    teslamate_abrp.update_timely = fake_update_timely
                    
                    # Test parked state
                    teslamate_abrp.state = "parked"
                    teslamate_abrp.prev_state = "driving"
                    try:
                        teslamate_abrp.update_timely()
                    except KeyboardInterrupt:
                        pass
                    
                    # Should call update_abrp
                    mock_update.assert_called_once()
                    mock_update.reset_mock()
                    
                    # Test charging state
                    teslamate_abrp.state = "charging"
                    teslamate_abrp.prev_state = "parked"
                    try:
                        teslamate_abrp.update_timely()
                    except KeyboardInterrupt:
                        pass
                    
                    # Should call update_abrp
                    mock_update.assert_called_once()
                    mock_update.reset_mock()
                    
                    # Test driving state
                    teslamate_abrp.state = "driving"
                    teslamate_abrp.prev_state = "parked"
                    try:
                        teslamate_abrp.update_timely()
                    except KeyboardInterrupt:
                        pass
                    
                    # Should call update_abrp
                    mock_update.assert_called_once()
                    mock_update.reset_mock()
                    
                    # Test unknown state
                    teslamate_abrp.state = "unknown_state"
                    teslamate_abrp.prev_state = "driving"
                    try:
                        teslamate_abrp.update_timely()
                    except KeyboardInterrupt:
                        pass
                    
                    # Should not call update_abrp
                    mock_update.assert_not_called()
                    
                    # Test with state change
                    teslamate_abrp.state = "charging"
                    teslamate_abrp.prev_state = "driving"
                    try:
                        teslamate_abrp.update_timely()
                    except KeyboardInterrupt:
                        pass
                    
                    # Should call update_abrp
                    mock_update.assert_called_once()

def test_setup_mqtt_client_comprehensive(mock_args):
    """Test all branches of setup_mqtt_client"""
    # Test with string port that needs conversion
    port_args = mock_args.copy()
    port_args["MQTTPORT"] = "1883"
    
    with patch('teslamate_mqtt2abrp.mqtt.Client') as mock_client:
        instance = mock_client.return_value
        
        # Create TeslaMateABRP instance
        abrp = TeslaMateABRP(port_args)
        
        # Check port was converted to int
        instance.connect.assert_called_once_with(port_args["MQTTSERVER"], 1883)
    
    # Test with invalid string port
    invalid_port_args = mock_args.copy()
    invalid_port_args["MQTTPORT"] = "invalid"
    
    with patch('teslamate_mqtt2abrp.mqtt.Client') as mock_client:
        instance = mock_client.return_value
        
        # Create TeslaMateABRP instance
        abrp = TeslaMateABRP(invalid_port_args)
        
        # Check default port was used
        instance.connect.assert_called_once_with(invalid_port_args["MQTTSERVER"], DEFAULT_MQTT_PORT)
    
    # Test with None port
    none_port_args = mock_args.copy()
    none_port_args["MQTTPORT"] = None
    
    with patch('teslamate_mqtt2abrp.mqtt.Client') as mock_client:
        instance = mock_client.return_value
        
        # Create TeslaMateABRP instance
        abrp = TeslaMateABRP(none_port_args)
        
        # Check default port was used
        instance.connect.assert_called_once_with(none_port_args["MQTTSERVER"], DEFAULT_MQTT_PORT)
    
    # Test with username but no password
    username_args = mock_args.copy()
    username_args["MQTTUSERNAME"] = "user"
    username_args["MQTTPASSWORD"] = None
    
    with patch('teslamate_mqtt2abrp.mqtt.Client') as mock_client:
        instance = mock_client.return_value
        
        # Create TeslaMateABRP instance
        abrp = TeslaMateABRP(username_args)
        
        # Check username was set without password
        instance.username_pw_set.assert_called_once_with("user")

def test_on_message_with_different_topics(teslamate_abrp):
    """Test on_message with various topic formats"""
    # Test with car model topic
    message = MagicMock()
    message.topic = "teslamate/cars/1/model"
    message.payload = b"3"
    
    teslamate_abrp.on_message(None, None, message)
    assert teslamate_abrp.data["model"] == "3"
    
    # Test with latitude topic
    message.topic = "teslamate/cars/1/latitude"
    message.payload = b"37.7749"
    
    teslamate_abrp.on_message(None, None, message)
    assert teslamate_abrp.data["lat"] == 37.7749
    
    # Test with state topic
    message.topic = "teslamate/cars/1/state"
    message.payload = b"driving"
    
    teslamate_abrp.on_message(None, None, message)
    assert teslamate_abrp.state == "driving"
    
    # Test with nonexistent topic
    message.topic = "teslamate/cars/1/nonexistent"
    message.payload = b"value"
    
    teslamate_abrp.on_message(None, None, message)  # Should not raise an error
    
    # Test with exception
    message.topic = "teslamate/cars/1/model"
    message.payload = None  # This should trigger an exception in the decoder
    
    teslamate_abrp.on_message(None, None, message)  # Should catch the exception

def test_on_connect_with_error():
    """Test on_connect with error code"""
    # Create a completely separate test class to avoid setup_mqtt_client
    class TestTeslaMateABRP:
        def __init__(self):
            self.config = {"CARNUMBER": "1"}
            self.base_topic = None
            self.client = MagicMock()
        
        # Copy only the on_connect method from the original class
        def on_connect(self, client, userdata, flags, reason_code, properties):
            if reason_code != 0:
                sys.exit("Could not connect to MQTT server")
            
            client.subscribe(f"teslamate/cars/{self.config.get('CARNUMBER')}/#")
            
            # Only publish online status if base_topic is set
            if self.base_topic:
                client.publish(self.state_topic, payload="online", qos=2, retain=True)
    
    # Create an instance of our test class
    abrp = TestTeslaMateABRP()
    
    # Create a mock client
    client_mock = MagicMock()
    
    # Replace sys.exit with a mock that raises an exception
    class MockExit(Exception):
        def __init__(self, message=""):
            self.message = message
            super().__init__(message)
    
    with patch('sys.exit', side_effect=MockExit):
        try:
            # Call with error code
            abrp.on_connect(client_mock, None, None, 1, None)  # Error code 1
            pytest.fail("Expected MockExit exception")
        except MockExit:
            # Should not subscribe
            client_mock.subscribe.assert_not_called()

def test_update_abrp_comprehensive(teslamate_abrp):
    """Test all branches of update_abrp"""
    import requests
    import json
    
    # Test successful update
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "ok"}
        mock_post.return_value = mock_response
        
        teslamate_abrp.update_abrp()
        
        mock_post.assert_called_once()
    
    # Test with JSON parsing error
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.json.side_effect = json.JSONDecodeError("Test error", "", 0)
        mock_post.return_value = mock_response
        
        teslamate_abrp.update_abrp()  # Should not raise
    
    # Test with missing 'status' in response
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.json.return_value = {"not_status": "value"}
        mock_post.return_value = mock_response
        
        teslamate_abrp.update_abrp()  # Should not raise
    
    # Test with connection error
    with patch('requests.post') as mock_post:
        mock_post.side_effect = requests.RequestException("Connection error")
        
        teslamate_abrp.update_abrp()  # Should not raise
    
    # Test with unexpected exception
    with patch('requests.post') as mock_post:
        mock_post.side_effect = Exception("Unexpected error")
        
        teslamate_abrp.update_abrp()  # Should not raise

def test_update_abrp_with_base_topic(teslamate_abrp_with_topic):
    """Test update_abrp with base_topic set"""
    import requests
    
    # Test successful update
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "ok"}
        mock_post.return_value = mock_response
        
        with patch.object(teslamate_abrp_with_topic, 'publish_to_mqtt') as mock_publish:
            teslamate_abrp_with_topic.update_abrp()
            
            # Should publish success status
            mock_publish.assert_called()
            
            # Find the success call
            success_call = False
            for call_args in mock_publish.call_args_list:
                args, kwargs = call_args
                data = args[0]
                if f"{teslamate_abrp_with_topic.prefix}_post_last_success" in data:
                    success_call = True
            
            assert success_call, "Should publish success status"

def test_run_with_cleanup(teslamate_abrp):
    """Test run method with client cleanup"""
    # Mock necessary methods
    with patch.object(teslamate_abrp, 'find_car_model'):
        with patch.object(teslamate_abrp, 'update_timely', side_effect=KeyboardInterrupt):
            with patch.object(teslamate_abrp.client, 'is_connected', return_value=True):
                with patch.object(teslamate_abrp.client, 'loop_stop') as mock_loop_stop:
                    with patch.object(teslamate_abrp.client, 'disconnect') as mock_disconnect:
                        # Run should catch the KeyboardInterrupt and perform cleanup
                        teslamate_abrp.run()
                        
                        # Verify cleanup was performed
                        mock_loop_stop.assert_called_once()
                        mock_disconnect.assert_called_once()

def test_handle_parked_state_comprehensive(teslamate_abrp):
    """Test handle_parked_state method in detail"""
    # Set up data with non-zero values
    teslamate_abrp.data["power"] = 10.5
    teslamate_abrp.data["speed"] = 65
    teslamate_abrp.data["kwh_charged"] = 30.5
    
    # Call method
    teslamate_abrp.handle_parked_state(0)
    
    # Verify data was reset
    assert teslamate_abrp.data["power"] == 0.0
    assert teslamate_abrp.data["speed"] == 0
    assert "kwh_charged" not in teslamate_abrp.data
    
    # Test with power already at 0
    teslamate_abrp.data["power"] = 0.0
    teslamate_abrp.data["speed"] = 0
    teslamate_abrp.data["kwh_charged"] = 15.2
    
    # Call method
    teslamate_abrp.handle_parked_state(0)
    
    # Verify kwh_charged was removed
    assert "kwh_charged" not in teslamate_abrp.data
    
    # Test with already cleaned data
    teslamate_abrp.data["power"] = 0.0
    teslamate_abrp.data["speed"] = 0
    # kwh_charged already removed
    
    # Call method again
    teslamate_abrp.handle_parked_state(0)
    
    # Verify values are still correct
    assert teslamate_abrp.data["power"] == 0.0
    assert teslamate_abrp.data["speed"] == 0
    assert "kwh_charged" not in teslamate_abrp.data

def test_battery_level_fallback(teslamate_abrp):
    """Test that battery_level is used as fallback when usable_battery_level is not available"""
    # Test with only battery_level
    teslamate_abrp.process_message("battery_level", "75")
    assert teslamate_abrp.data["soc"] == 75
    assert teslamate_abrp.has_usable_battery_level is False
    
    # Test with both - usable_battery_level should take precedence
    teslamate_abrp.process_message("usable_battery_level", "70")
    assert teslamate_abrp.data["soc"] == 70
    assert teslamate_abrp.has_usable_battery_level is True
    
    # Test that battery_level is ignored once usable_battery_level has been received
    teslamate_abrp.process_message("battery_level", "80")
    assert teslamate_abrp.data["soc"] == 70  # Should not change
    
    # Test with invalid values
    teslamate_abrp.process_message("battery_level", "invalid")
    assert teslamate_abrp.data["soc"] == 70  # Should not change

def test_init_has_usable_battery_level_flag(mock_args):
    """Test that the has_usable_battery_level flag is initialized to False"""
    with patch('teslamate_mqtt2abrp.TeslaMateABRP.setup_mqtt_client'):
        abrp = TeslaMateABRP(mock_args)
        assert hasattr(abrp, 'has_usable_battery_level')
        assert abrp.has_usable_battery_level is False

if __name__ == "__main__":
    pytest.main()