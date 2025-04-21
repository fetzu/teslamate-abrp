"""
TeslaMate MQTT to ABRP:
A slightly convoluted way of getting your vehicle data from TeslaMate to A Better Route Planner.
"""

## [ IMPORTS ]
import sys
import datetime
import calendar
import os
import logging
import requests
import json
import paho.mqtt.client as mqtt
import click
from time import sleep
from typing import Dict, Any, Optional

## [ CONFIGURATION ]
APIKEY = "d49234a5-2553-454e-8321-3fe30b49aa64"
DEFAULT_MQTT_PORT = 1883
DEFAULT_CAR_NUMBER = 1

# Refresh rates (in seconds)
REFRESH_RATE_DRIVING = 1
REFRESH_RATE_CHARGING = 6
REFRESH_RATE_PARKED = 30

# Tesla model ID mapping
MODEL_MAPPINGS = {
    "3": {
        "50": "3standard",
        "62": "3mid",
        "74": "3long",
        "74D": "3long_awd",
        "P74D": "3p20"
    },
    "Y": {
        "74D": "tesla:my:19:bt37:awd",
        "P74D": "tesla:my:19:bt37:perf",
        "50": "tesla:my:22:my_lfp:rwd"
    }
}

## [ la CLASSe américaine ]
class TeslaMateABRP:
    def __init__(self, config):
        self.config = config
        self.configure_logging()
        self.base_topic = self.config.get("BASETOPIC")
        self.prefix = "_tm2abrp"
        # Only set state_topic if base_topic is provided
        self.state_topic = f"{self.base_topic}/{self.prefix}_status" if self.base_topic else None
        
        self.setup_mqtt_client()
        self.state = ""
        self.prev_state = ""
        self.charger_phases = 1
        self.has_usable_battery_level = False  # Flag to track if we've received usable_battery_level
        
        # Default data structure for ABRP
        self.data = {
            "utc": 0,
            "soc": 0,
            "power": 0,
            "speed": 0,
            "lat": 0,
            "lon": 0,
            "elevation": 0,
            "is_charging": False,
            "is_dcfc": False,
            "is_parked": False,
            "est_battery_range": 0,
            "ideal_battery_range": 0,
            "ext_temp": 0,
            "model": "",
            "trim_badging": "",
            "car_model": self.config.get("CARMODEL", ""),
            "tlm_type": "api",
            "voltage": 0,
            "current": 0,
            "kwh_charged": 0,
            "heading": 0
        }

    def process_message(self, topic: str, payload: str):
        """Process individual MQTT message based on topic name."""

        # Skip empty payloads for most topics
        if not payload and topic not in ["shift_state", "state"]:
            return

        if topic == "model":
            self.data["model"] = payload
        elif topic == "trim_badging":
            self.data["trim_badging"] = payload
        elif topic == "latitude" and not self.config.get("SKIPLOCATION"):
            try:
                self.data["lat"] = float(payload)
            except ValueError:
                pass
        elif topic == "longitude" and not self.config.get("SKIPLOCATION"):
            try:
                self.data["lon"] = float(payload)
            except ValueError:
                pass
        elif topic == "elevation":
            try:
                self.data["elevation"] = int(payload)
            except ValueError:
                pass
        elif topic == "speed":
            try:
                self.data["speed"] = int(payload)
            except ValueError:
                pass
        elif topic == "power":
            try:
                self.data["power"] = float(payload)
                if self.data["is_charging"] and float(payload) < -11:
                    self.data["is_dcfc"] = True
            except ValueError:
                pass
        elif topic == "charger_power":
            try:
                if payload and int(payload) != 0:
                    self.data["is_charging"] = True
                    if int(payload) > 11:
                        self.data["is_dcfc"] = True
            except ValueError:
                pass
        elif topic == "heading":
            try:
                self.data["heading"] = int(payload)
            except ValueError:
                pass
        elif topic == "outside_temp":
            try:
                self.data["ext_temp"] = float(payload)
            except ValueError:
                pass
        elif topic == "odometer":
            try:
                self.data["odometer"] = float(payload)
            except ValueError:
                pass
        elif topic == "ideal_battery_range_km":
            try:
                self.data["ideal_battery_range"] = float(payload)
            except ValueError:
                pass
        elif topic == "est_battery_range_km":
            try:
                self.data["est_battery_range"] = float(payload)
            except ValueError:
                pass
        elif topic == "charger_actual_current":
            try:
                if payload and int(payload) > 0:
                    self.data["current"] = int(payload)
                else:
                    self.data["current"] = 0
                    self.data.pop("current", None)
            except ValueError:
                pass
        elif topic == "charger_voltage":
            try:
                if payload and int(payload) > 5:
                    self.data["voltage"] = int(payload)
                else:
                    self.data["voltage"] = 0
                    self.data.pop("voltage", None)
            except ValueError:
                pass
        elif topic == "shift_state":
            if payload == "P":
                self.data["is_parked"] = True
            elif payload in ["D", "R", "N"]:
                self.data["is_parked"] = False
        elif topic == "state":
            self.state = payload
            self.handle_state_change(payload)
        elif topic == "usable_battery_level":
            try:
                self.data["soc"] = int(payload)
                self.has_usable_battery_level = True
            except ValueError:
                pass
        elif topic == "battery_level":
            try:
                # Only use battery_level if we haven't received usable_battery_level
                if not self.has_usable_battery_level:
                    self.data["soc"] = int(payload)
            except ValueError:
                pass
        elif topic == "charge_energy_added":
            try:
                self.data["kwh_charged"] = float(payload)
            except ValueError:
                pass
        elif topic == "charger_phases":
            try:
                self.charger_phases = 3 if payload and int(payload) > 1 else 1
            except ValueError:
                pass
        else:
            # Unhandled topic
            logging.debug(f"Unneeded topic: {topic} {payload}")

        # Calculate accurate power on AC charging
        if self.data["is_charging"] and not self.data["is_dcfc"] and "voltage" in self.data and "current" in self.data:
            self.data["power"] = (float(self.data["current"] * self.data["voltage"] * self.charger_phases) 
                             / 1000.0 * -1)

    def handle_state_change(self, state: str):
        """Update car state and relevant data fields."""
        if state == "driving":
            self.data["is_parked"] = False
            self.data["is_charging"] = False
            self.data["is_dcfc"] = False
        elif state == "charging":
            self.data["is_parked"] = True
            self.data["is_charging"] = True
            self.data["is_dcfc"] = False
        elif state == "supercharging":
            self.data["is_parked"] = True
            self.data["is_charging"] = True
            self.data["is_dcfc"] = True
        elif state in ["online", "suspended", "asleep", "offline"]:
            self.data["is_parked"] = True
            self.data["is_charging"] = False
            self.data["is_dcfc"] = False

    def find_car_model(self):
        """Determine car model from TeslaMate data."""
        sleep(10)  # Wait to receive initial messages

        # Handle Model 3 and Y using mapping dictionary
        if self.data["model"] in MODEL_MAPPINGS and self.data["trim_badging"] in MODEL_MAPPINGS[self.data["model"]]:
            self.data["car_model"] = MODEL_MAPPINGS[self.data["model"]][self.data["trim_badging"]]
        # Handle simple cases (Model S and X)
        elif self.data["model"] in ["S", "X"]:
            self.data["car_model"] = f"{self.data['model'].lower()}{self.data['trim_badging'].lower()}"
        # Log warning for unknown models
        else:
            logging.warning(
                f"Your {self.data['model']} trim could not be automatically determined. "
                f"Trim reported as: {self.data['trim_badging']}."
            )
            return

        if self.data["car_model"]:
            logging.info(f"Car model automatically determined as: {self.data['car_model']}.")
        else:
            logging.warning(
                "Car model could not be automatically determined, "
                "please set it through the CLI or environment var according to the documentation for best results."
            )

    def publish_to_mqtt(self, data_object: Dict[str, Any]):
        """Publish data to MQTT topics."""
        # Only publish if base_topic is set
        if not self.base_topic:
            return
            
        logging.debug(f"Publishing to MQTT: {data_object}")
        for key, value in data_object.items():
            try:
                self.client.publish(
                    f"{self.base_topic}/{key}",
                    payload=value,
                    qos=1,
                    retain=True
                )
            except Exception as e:
                logging.error(f"Failed to publish to MQTT: {e}")

    def update_abrp(self):
        """Send data to ABRP API."""
        try:
            headers = {"Authorization": f"APIKEY {APIKEY}"}
            body = {"tlm": self.data}
            response = requests.post(
                f"https://api.iternio.com/1/tlm/send?token={self.config.get('USERTOKEN')}", 
                headers=headers, 
                json=body,
                timeout=10
            )

            try:
                resp = response.json()
                if self.base_topic:
                    self.publish_to_mqtt({f"{self.prefix}_post_last_status": resp["status"]})
                
                if resp["status"] != "ok":
                    logging.error(f"Error, response from the ABRP API: {response.text}.")
                    if self.base_topic:
                        self.publish_to_mqtt({f"{self.prefix}_post_last_error": self.nice_now()})
                else:
                    logging.info(f"Data object successfully sent: {self.data}")
                    if self.base_topic:
                        self.publish_to_mqtt({f"{self.prefix}_post_last_success": self.nice_now()})
            except (json.JSONDecodeError, KeyError) as e:
                logging.error(f"Invalid response from ABRP API: {e}")
                if self.base_topic:
                    self.publish_to_mqtt({f"{self.prefix}_post_last_error": self.nice_now()})

        except requests.RequestException as ex:
            logging.critical(f"Failed to connect to ABRP API: {ex}")
            if self.base_topic:
                self.publish_to_mqtt({f"{self.prefix}_post_exception": str(ex)})
                self.publish_to_mqtt({f"{self.prefix}_post_last_exception": self.nice_now()})
        except Exception as ex:
            logging.critical(
                f"Unexpected exception while POSTing to ABRP API: {type(ex).__name__} - {ex}"
            )
            if self.base_topic:
                self.publish_to_mqtt({f"{self.prefix}_post_exception": str(ex)})
                self.publish_to_mqtt({f"{self.prefix}_post_last_exception": self.nice_now()})

    def nice_now(self) -> str:
        """Return a formatted timestamp."""
        return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")

    def update_timely(self):
        """Update ABRP based on car state and timers."""
        i = -1
        while True:
            i += 1
            sleep(1)  # Base refresh rate

            # Reset counter when state changes
            if self.state != self.prev_state:
                i = max(REFRESH_RATE_PARKED, REFRESH_RATE_CHARGING, REFRESH_RATE_DRIVING)
                logging.debug(f"Current car state changed to: {self.state}.")

            # Update UTC timestamp
            self.data["utc"] = calendar.timegm(datetime.datetime.now(datetime.UTC).timetuple())

            # Handle different car states
            if self.state in ["parked", "online", "suspended", "asleep", "offline"]:
                self.handle_parked_state(i)
                if i % REFRESH_RATE_PARKED == 0 or i > REFRESH_RATE_PARKED:
                    if self.prev_state != self.state:
                        logging.info(f"Car is sleeping, updating every {REFRESH_RATE_PARKED}s.")
                    self.update_abrp()
                    if self.base_topic:
                        self.publish_to_mqtt(self.data)
                    i = 0
            elif self.state == "charging":
                if i % REFRESH_RATE_CHARGING == 0:
                    if self.prev_state != self.state:
                        logging.info(f"Car is charging, updating every {REFRESH_RATE_CHARGING}s.")
                    self.update_abrp()
                    if self.base_topic:
                        self.publish_to_mqtt(self.data)
            elif self.state == "driving":
                if self.prev_state != self.state:
                    logging.info(f"Car is driving, updating every {REFRESH_RATE_DRIVING}s.")
                self.update_abrp()
                if self.base_topic:
                    self.publish_to_mqtt(self.data)
            elif self.state:  # Any other non-empty state
                logging.error(f"Car is in unknown state ({self.state}), not sending any update to ABRP.")
                
            self.prev_state = self.state

    def handle_parked_state(self, counter: int):
        """Handle data updates when car is parked."""
        # Reset power and speed if they're not zero
        if self.data["power"] != 0:
            self.data["power"] = 0.0
        if self.data["speed"] > 0:
            self.data["speed"] = 0
        # Remove kwh_charged field when not charging
        if "kwh_charged" in self.data:
            self.data.pop("kwh_charged", None)
 
    def run(self):
        """Main entry point to run the application."""
        # If car model not provided, try to determine it
        if not self.config.get("CARMODEL"):
            self.find_car_model()
        else:
            logging.info(f"Car model manually set to: {self.config.get('CARMODEL')}.")
        
        try:
            # Start the main update loop
            self.update_timely()
        except KeyboardInterrupt:
            logging.info("Received keyboard interrupt, shutting down.")
        finally:
            # Clean up
            if self.client.is_connected():
                self.client.loop_stop()
                self.client.disconnect()
            logging.info("Shutdown complete.")

def get_docker_secret(secret_name: str) -> Optional[str]:
    """Read a secret from Docker secrets directory."""
    file_path = f"/run/secrets/{secret_name}"
    if os.path.isfile(file_path):
        try:
            with open(file_path, "r") as f:
                content = f.read().splitlines()
                if content and content[0]:
                    return content[0]
        except Exception as e:
            logging.error(f"Error reading docker secret {secret_name}: {e}")
    return None

## [ Click CLI Implementation ]
@click.command(help="A slightly convoluted way of getting your vehicle data from TeslaMate to A Better Route Planner.")
@click.argument('user_token', required=False, envvar='USER_TOKEN')
@click.argument('car_number', required=False, envvar='CAR_NUMBER')
@click.argument('mqtt_server', required=False, envvar='MQTT_SERVER')
@click.argument('mqtt_username', required=False, envvar='MQTT_USERNAME')
@click.argument('mqtt_password', required=False, envvar='MQTT_PASSWORD')
@click.argument('mqtt_port', required=False, type=int, envvar='MQTT_PORT')
@click.option('--model', 'car_model', envvar='CAR_MODEL', 
             help='Car model according to https://api.iternio.com/1/tlm/get_CARMODELs_list')
@click.option('--status-topic', 'status_topic', envvar='STATUS_TOPIC',
             help='MQTT topic to publish status messages to')
@click.option('-d', '--debug', is_flag=True, envvar='TM2ABRP_DEBUG',
             help='Debug mode (set logging level to DEBUG)')
@click.option('-a', '--auth', 'use_auth', is_flag=True,
             help='Use authentication (username and password) to connect to MQTT server')
@click.option('-s', '--use-tls', 'use_tls', is_flag=True, envvar='MQTT_TLS',
             help='Use TLS to connect to MQTT server')
@click.option('-x', '--skip-location', 'skip_location', is_flag=True, envvar='SKIP_LOCATION',
             help="Don't send LAT and LON to ABRP")
def main(user_token, car_number, mqtt_server, mqtt_username, mqtt_password, mqtt_port,
         car_model, status_topic, debug, use_auth, use_tls, skip_location):
    """teslamate-abrp

    A slightly convoluted way of getting your vehicle data from TeslaMate to A Better Route Planner.
    
    Arguments can be provided as command-line arguments or as environment variables.
    """
    # Initialize config dictionary
    config = {}

    # Check for Docker secrets first for sensitive data
    if not user_token:
        docker_token = get_docker_secret('USER_TOKEN')
        if docker_token:
            user_token = docker_token
            logging.debug("Using USER_TOKEN from Docker secret")

    # Check for MQTT password in Docker secrets
    if not mqtt_password:
        docker_password = get_docker_secret('MQTT_PASSWORD')
        if docker_password:
            mqtt_password = docker_password
            logging.debug("Using MQTT_PASSWORD from Docker secret")

    # Required arguments checks
    if not mqtt_server:
        click.echo("MQTT server address not supplied. Please supply through environment variables or CLI argument.")
        sys.exit(1)

    if not user_token:
        click.echo("User token not supplied. Please generate it through ABRP and supply through environment variables or CLI argument.")
        sys.exit(1)

    # Set up configuration dict
    config["MQTTSERVER"] = mqtt_server
    config["USERTOKEN"] = user_token
    config["CARNUMBER"] = car_number or DEFAULT_CAR_NUMBER
    
    # Convert MQTT port properly with warning
    try:
        config["MQTTPORT"] = int(mqtt_port) if mqtt_port else DEFAULT_MQTT_PORT
    except (ValueError, TypeError):
        logging.warning(f"Invalid MQTT port provided: {mqtt_port}. Using default: {DEFAULT_MQTT_PORT}")
        config["MQTTPORT"] = DEFAULT_MQTT_PORT
    
    # Handle authentication more clearly
    config["MQTTUSERNAME"] = mqtt_username
    config["MQTTPASSWORD"] = mqtt_password if use_auth else None
    config["MQTTTLS"] = use_tls
    config["CARMODEL"] = car_model
    config["BASETOPIC"] = status_topic
    config["SKIPLOCATION"] = skip_location
    config["DEBUG"] = debug

    # Run the application
    try:
        teslamate_abrp = TeslaMateABRP(config)
        teslamate_abrp.run()
    except KeyboardInterrupt:
        logging.info("Program terminated by user")
        sys.exit(0)
    except Exception as e:
        logging.critical(f"Unhandled exception: {e}")
        sys.exit(1)

## [ MAIN ]
if __name__ == '__main__':
    main()