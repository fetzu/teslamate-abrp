"""
TeslaMate MQTT to ABRP:
A slightly convoluted way of getting your vehicle data from TeslaMate to A Better Route Planner.
"""

## [ IMPORTS ]
import sys
import datetime
import calendar
import os
import re
import math
import threading
import logging
import requests
import json
import paho.mqtt.client as mqtt
import click
from time import sleep
from typing import Dict, Any, Optional

## [ CONFIGURATION ]
# Shared ABRP "Generic" application key. This is NOT a per-user secret - it
# identifies the integration, and every user supplies their own USER_TOKEN.
# It can be overridden at deploy time via the ABRP_API_KEY env var or a Docker
# secret of the same name; the literal below is the default fallback.
APIKEY = "d49234a5-2553-454e-8321-3fe30b49aa64"
DEFAULT_MQTT_PORT = 1883
DEFAULT_CAR_NUMBER = 1

# Refresh rates (in seconds) - used as fallback defaults when not configured.
# Driving default is 2s: ABRP recommends a data point roughly every 5s and says
# faster updates don't materially improve its predictions, so 2s stays responsive
# while cutting redundant POSTs vs the old 1s default. (Must be whole seconds:
# the update loop uses a 1s tick with integer-modulo scheduling.)
DEFAULT_REFRESH_RATE_DRIVING = 2
DEFAULT_REFRESH_RATE_CHARGING = 6
DEFAULT_REFRESH_RATE_PARKED = 30

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

def validate_refresh_rate(value: Any, default: int, name: str) -> int:
    """Validate a refresh rate, falling back to the default for missing/invalid values.

    Refresh rates must be whole positive seconds (the update loop uses them as a
    modulo divisor, so 0 or negative values are rejected).
    """
    if value is None:
        return default
    try:
        rate = int(value)
        if rate < 1:
            raise ValueError
        return rate
    except (ValueError, TypeError):
        logging.warning(f"Invalid {name} refresh rate provided: {value}. Using default: {default}s.")
        return default


# Matches a `token=<value>` query-string parameter so the ABRP user token can be
# stripped out of anything that gets logged or published (e.g. requests/urllib3
# exception strings embed the full request URL, which carries the token).
_TOKEN_QS_RE = re.compile(r'(token=)[^&\s]+', re.IGNORECASE)


def redact_secrets(text: Any) -> str:
    """Redact secret query-string values (notably the ABRP user token) from a
    string before it is logged or published to MQTT."""
    return _TOKEN_QS_RE.sub(r'\1REDACTED', str(text))


def parse_bool_env(name: str, default: bool) -> bool:
    """Parse a boolean environment variable.

    Unset or unrecognised values fall back to ``default`` (fail-secure for the
    TLS-verification flag) rather than aborting the program.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on", "y", "t"):
        return True
    if normalized in ("0", "false", "no", "off", "n", "f", ""):
        return False
    logging.warning(f"Invalid {name} value: {value!r}. Using default: {default}.")
    return default

## [ la CLASSe américaine ]
class TeslaMateABRP:
    def __init__(self, config):
        self.config = config
        self.configure_logging()
        self.base_topic = self.config.get("BASETOPIC")
        self.prefix = "_tm2abrp"
        # ABRP application key: config override (env/Docker secret) or the
        # shared default. Not a per-user secret (see APIKEY above).
        self.api_key = self.config.get("APIKEY") or APIKEY
        # Only set state_topic if base_topic is provided
        self.state_topic = f"{self.base_topic}/{self.prefix}_status" if self.base_topic else None
        
        self.setup_mqtt_client()
        self.state = ""
        self.prev_state = ""
        self.charger_phases = 1
        self.has_usable_battery_level = False  # Flag to track if we've received usable_battery_level
        # Guards self.data: it is mutated by the paho callback thread (on_message)
        # and read/serialized by the main update loop.
        self.data_lock = threading.Lock()
        # Last value published per MQTT key, so unchanged values aren't
        # republished every cycle (retain=True already holds them on the broker).
        self.last_published: Dict[str, Any] = {}
        # Set from the paho callback thread (on_connect) to request a shutdown
        # that must happen on the main thread.
        self.fatal_error: Optional[str] = None

        # Refresh rates (in seconds), validated with fallback to defaults
        self.refresh_rate_driving = validate_refresh_rate(
            self.config.get("REFRESH_RATE_DRIVING"), DEFAULT_REFRESH_RATE_DRIVING, "driving"
        )
        self.refresh_rate_charging = validate_refresh_rate(
            self.config.get("REFRESH_RATE_CHARGING"), DEFAULT_REFRESH_RATE_CHARGING, "charging"
        )
        self.refresh_rate_parked = validate_refresh_rate(
            self.config.get("REFRESH_RATE_PARKED"), DEFAULT_REFRESH_RATE_PARKED, "parked"
        )
        
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

    def configure_logging(self):
        log_level = logging.DEBUG if self.config.get("DEBUG") else logging.INFO
        logging.basicConfig(
            format='%(asctime)s: [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            level=log_level
        )
        # urllib3 logs the full request URL at DEBUG, which carries the ABRP
        # user token in the query string. Keep it quiet regardless of app level
        # so enabling --debug never leaks the token into the logs.
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        if log_level == logging.DEBUG:
            logging.debug("Logging level set to DEBUG.")

    def setup_mqtt_client(self):
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, 
            f"teslamateToABRP-{self.config.get('CARNUMBER')}"
        )

        # Set up authentication based on available credentials
        mqtt_username = self.config.get("MQTTUSERNAME")
        mqtt_password = self.config.get("MQTTPASSWORD")
        
        if mqtt_username and mqtt_password:
            logging.debug(f"Using MQTT authentication with username: {mqtt_username} and password")
            self.client.username_pw_set(mqtt_username, mqtt_password)
        elif mqtt_username:
            logging.debug(f"Using MQTT username only: {mqtt_username} (no password)")
            self.client.username_pw_set(mqtt_username)
        else:
            logging.debug("No MQTT authentication configured")

        # Set up TLS if needed with better error handling
        if self.config.get("MQTTTLS"):
            try:
                import ssl
                logging.debug("Using TLS with MQTT")
                verify_cert = self.config.get("MQTT_VERIFY_CERT", True)
                cert_reqs = ssl.CERT_REQUIRED if verify_cert else ssl.CERT_NONE
                self.client.tls_set(cert_reqs=cert_reqs)
                logging.debug(f"TLS configured with certificate verification: {verify_cert}")
            except ImportError:
                logging.error("SSL module not available. TLS cannot be enabled.")
            except Exception as e:
                logging.error(f"Failed to configure TLS: {e}")

        # Set up last will if base topic is set
        if self.base_topic:
            logging.debug(f"Using MQTT base topic: {self.base_topic} for last will")
            self.client.will_set(self.state_topic, payload="offline", qos=2, retain=True)

        # Set up callbacks
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect

        # Connect to MQTT server with better error handling
        mqtt_port = self.config.get("MQTTPORT", DEFAULT_MQTT_PORT)
        mqtt_server = self.config.get("MQTTSERVER")
        
        # Convert port to integer
        try:
            mqtt_port = int(mqtt_port)
        except (ValueError, TypeError):
            logging.warning(f"Invalid MQTT port provided: {mqtt_port}. Using default: {DEFAULT_MQTT_PORT}")
            mqtt_port = DEFAULT_MQTT_PORT
        
        logging.debug(f"Attempting to connect to MQTT server: {mqtt_server}:{mqtt_port}")
        
        try:
            self.client.connect(mqtt_server, mqtt_port)
            self.client.loop_start()
            logging.debug("MQTT client connection started successfully")
        except ConnectionRefusedError:
            error_msg = f"Connection refused to MQTT server {mqtt_server}:{mqtt_port}. Check if the server is running and accessible."
            logging.critical(error_msg)
            sys.exit(error_msg)
        except TimeoutError:
            error_msg = f"Connection timeout to MQTT server {mqtt_server}:{mqtt_port}. Check network connectivity and firewall settings."
            logging.critical(error_msg)
            sys.exit(error_msg)
        except Exception as e:
            error_msg = f"Failed to connect to MQTT server: {e}"
            logging.critical(error_msg)
            sys.exit(error_msg)

    def on_connect(self, client, userdata, flags, reason_code, properties):
        result_str = mqtt.connack_string(reason_code)
        logging.info(f"MQTT Connection returned result: {result_str} (reason code {reason_code}).")
        
        # Improved authentication failure detection for both MQTT v3.1.1 and v5.
        # NOTE: on_connect runs in paho's network-loop thread, so calling
        # sys.exit() here would only raise SystemExit in that thread and leave
        # the main update loop spinning forever. Record the fatal reason instead
        # and let the main thread (update_timely) perform the shutdown.
        if reason_code == 5 or reason_code == 4:  # Auth failure codes
            error_msg = "MQTT Authentication failed. Check your username and password."
            logging.critical(error_msg)
            self.fatal_error = error_msg
            return
        elif reason_code == 3:  # Server unavailable
            error_msg = "MQTT Broker unavailable. Check if the server is running."
            logging.critical(error_msg)
            self.fatal_error = error_msg
            return
        elif reason_code == 2:  # Client identifier rejected
            error_msg = "MQTT Client ID rejected. Try using a different client ID."
            logging.critical(error_msg)
            self.fatal_error = error_msg
            return
        elif reason_code != 0:
            error_msg = f"Could not connect to MQTT server. Reason: {result_str} (code {reason_code})"
            logging.critical(error_msg)
            self.fatal_error = error_msg
            return
        
        logging.debug("MQTT connection successful, subscribing to topics...")
        client.subscribe(f"teslamate/cars/{self.config.get('CARNUMBER')}/#")
        logging.debug(f"Subscribed to teslamate/cars/{self.config.get('CARNUMBER')}/#")

        # Only publish online status if base_topic is set
        if self.base_topic:
            client.publish(self.state_topic, payload="online", qos=2, retain=True)
            logging.debug(f"Published 'online' status to {self.state_topic}")

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        # paho's background network loop auto-reconnects; until it does,
        # update_abrp() skips sending so stale telemetry isn't reported as live.
        # A clean shutdown (reason_code 0) is logged at a lower level.
        if reason_code == 0:
            logging.debug("MQTT client disconnected cleanly.")
        else:
            logging.warning(
                f"MQTT client disconnected unexpectedly (reason code {reason_code}). "
                f"Pausing ABRP updates until the connection is restored."
            )

    def on_message(self, client, userdata, message):
        try:
            payload = str(message.payload.decode("utf-8"))
            topic_name = message.topic.split('/')[-1]

            # Hold the data lock for the whole message: process_message and
            # handle_state_change mutate self.data, which the main loop reads.
            with self.data_lock:
                self.process_message(topic_name, payload)

        except Exception as e:
            logging.critical(
                f"Unexpected exception while processing message: {type(e).__name__} - {e}, "
                f"topic: {message.topic}, payload: {message.payload}"
            )

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
                value = float(payload)
                # Reject non-finite values (nan/inf): json(allow_nan=False) would
                # otherwise reject the whole payload and break every later POST.
                if not math.isfinite(value):
                    raise ValueError("non-finite power")
                self.data["power"] = value
                if self.data["is_charging"] and value < -11:
                    self.data["is_dcfc"] = True
            except ValueError:
                pass
        elif topic == "charger_power":
            try:
                if payload and int(payload) != 0:
                    self.data["is_charging"] = True
                    if int(payload) > 11:
                        self.data["is_dcfc"] = True
                elif payload and int(payload) == 0:
                    # Charger power dropped to 0: clear the charge flags so a
                    # missed 'state' transition can't latch stale charging flags.
                    self.data["is_charging"] = False
                    self.data["is_dcfc"] = False
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
            # Guard against OverflowError from absurdly large (malformed) values.
            try:
                self.data["power"] = (float(self.data["current"] * self.data["voltage"] * self.charger_phases)
                                      / 1000.0 * -1)
            except (OverflowError, ValueError):
                pass

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
        # Snapshot under the lock so a concurrent mutation of self.data on the
        # MQTT callback thread can't raise "dictionary changed size during
        # iteration" here on the main thread.
        with self.data_lock:
            items = list(data_object.items())
        for key, value in items:
            # Skip republishing unchanged values: retain=True already keeps the
            # last value on the broker, so this only drops redundant traffic
            # (e.g. while parked only `utc` changes, not all ~21 fields).
            if key in self.last_published and self.last_published[key] == value:
                continue
            try:
                self.client.publish(
                    f"{self.base_topic}/{key}",
                    payload=value,
                    qos=1,
                    retain=True
                )
                self.last_published[key] = value
            except Exception as e:
                logging.error(f"Failed to publish to MQTT: {e}")

    def update_abrp(self):
        """Send data to ABRP API."""
        # Don't POST while the MQTT link is down: self.state/self.data are frozen
        # at their last-known values and would be reported to ABRP as if live.
        # paho's background loop auto-reconnects (see on_disconnect).
        if not self.client.is_connected():
            logging.debug("MQTT not connected; skipping ABRP update to avoid sending stale data.")
            return
        try:
            headers = {"Authorization": f"APIKEY {self.api_key}"}
            # Snapshot under the lock so the payload can't change mid-serialize.
            # Stamp the send time here (P-3) rather than on every idle loop tick.
            with self.data_lock:
                self.data["utc"] = calendar.timegm(datetime.datetime.now(datetime.UTC).timetuple())
                snapshot = dict(self.data)
            # Defense-in-depth: drop any non-finite numbers (nan/inf) so json
            # (allow_nan=False) can't reject the whole payload and break every
            # subsequent POST until the offending value happens to change.
            snapshot = {
                k: v for k, v in snapshot.items()
                if not (isinstance(v, float) and not math.isfinite(v))
            }
            body = {"tlm": snapshot}
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
                    logging.error(f"Error, response from the ABRP API: {redact_secrets(response.text)}.")
                    if self.base_topic:
                        self.publish_to_mqtt({f"{self.prefix}_post_last_error": self.nice_now()})
                else:
                    # Keep an INFO heartbeat without PII; the full payload
                    # (lat/lon/odometer) is only emitted at DEBUG.
                    logging.info(
                        f"Data sent to ABRP (soc={self.data.get('soc')}%, "
                        f"state={self.state or 'unknown'})."
                    )
                    logging.debug(f"Full data object sent: {self.data}")
                    if self.base_topic:
                        self.publish_to_mqtt({f"{self.prefix}_post_last_success": self.nice_now()})
            except (json.JSONDecodeError, KeyError) as e:
                logging.error(f"Invalid response from ABRP API: {e}")
                if self.base_topic:
                    self.publish_to_mqtt({f"{self.prefix}_post_last_error": self.nice_now()})

        except requests.RequestException as ex:
            logging.critical(f"Failed to connect to ABRP API: {redact_secrets(ex)}")
            if self.base_topic:
                self.publish_to_mqtt({f"{self.prefix}_post_exception": redact_secrets(ex)})
                self.publish_to_mqtt({f"{self.prefix}_post_last_exception": self.nice_now()})
        except Exception as ex:
            logging.critical(
                f"Unexpected exception while POSTing to ABRP API: {type(ex).__name__} - {redact_secrets(ex)}"
            )
            if self.base_topic:
                self.publish_to_mqtt({f"{self.prefix}_post_exception": redact_secrets(ex)})
                self.publish_to_mqtt({f"{self.prefix}_post_last_exception": self.nice_now()})

    def nice_now(self) -> str:
        """Return a formatted timestamp."""
        return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")

    def update_timely(self):
        """Update ABRP based on car state and timers."""
        i = -1
        while True:
            # A fatal MQTT failure is flagged from the callback thread; exit the
            # process from the main thread so it doesn't spin here forever and
            # the container runtime can apply its restart policy.
            if self.fatal_error:
                raise SystemExit(self.fatal_error)
            i += 1
            sleep(1)  # Base refresh rate

            # Reset counter when state changes so the first update fires promptly
            # (0 % rate == 0 for any rate, regardless of the configured values)
            if self.state != self.prev_state:
                i = 0
                logging.debug(f"Current car state changed to: {self.state}.")

            # (utc is stamped inside update_abrp at actual send time, not here.)

            # Handle different car states
            if self.state in ["parked", "online", "suspended", "asleep", "offline"]:
                self.handle_parked_state(i)
                if i % self.refresh_rate_parked == 0:
                    if self.prev_state != self.state:
                        logging.info(f"Car is sleeping, updating every {self.refresh_rate_parked}s.")
                    self.update_abrp()
                    if self.base_topic:
                        self.publish_to_mqtt(self.data)
                    i = 0
            elif self.state == "charging":
                if i % self.refresh_rate_charging == 0:
                    if self.prev_state != self.state:
                        logging.info(f"Car is charging, updating every {self.refresh_rate_charging}s.")
                    self.update_abrp()
                    if self.base_topic:
                        self.publish_to_mqtt(self.data)
            elif self.state == "driving":
                if i % self.refresh_rate_driving == 0:
                    if self.prev_state != self.state:
                        logging.info(f"Car is driving, updating every {self.refresh_rate_driving}s.")
                    self.update_abrp()
                    if self.base_topic:
                        self.publish_to_mqtt(self.data)
            elif self.state:  # Any other non-empty state
                logging.error(f"Car is in unknown state ({self.state}), not sending any update to ABRP.")
                
            self.prev_state = self.state

    def handle_parked_state(self, counter: int):
        """Handle data updates when car is parked."""
        with self.data_lock:
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
                    return content[0].strip()  # Strip whitespace to avoid issues
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
@click.option('-a', '--auth', 'use_auth', is_flag=True, envvar='MQTT_AUTH',
             help='Use authentication (username and password) to connect to MQTT server')
@click.option('-s', '--use-tls', 'use_tls', is_flag=True, envvar='MQTT_TLS',
             help='Use TLS to connect to MQTT server')
@click.option('--verify-cert/--no-verify-cert', 'verify_cert', default=None,
             help='Verify MQTT TLS certificates (default: enabled). '
                  'Env var MQTT_VERIFY_CERT also accepted; invalid values fall back to enabled.')
@click.option('-x', '--skip-location', 'skip_location', is_flag=True, envvar='SKIP_LOCATION',
             help="Don't send LAT and LON to ABRP")
@click.option('--refresh-driving', 'refresh_driving', type=int, envvar='REFRESH_RATE_DRIVING',
             help=f'Update interval in seconds while driving (default: {DEFAULT_REFRESH_RATE_DRIVING})')
@click.option('--refresh-charging', 'refresh_charging', type=int, envvar='REFRESH_RATE_CHARGING',
             help=f'Update interval in seconds while charging (default: {DEFAULT_REFRESH_RATE_CHARGING})')
@click.option('--refresh-parked', 'refresh_parked', type=int, envvar='REFRESH_RATE_PARKED',
             help=f'Update interval in seconds while parked/asleep (default: {DEFAULT_REFRESH_RATE_PARKED})')

def main(user_token, car_number, mqtt_server, mqtt_username, mqtt_password, mqtt_port,
         car_model, status_topic, debug, use_auth, use_tls, verify_cert, skip_location,
         refresh_driving, refresh_charging, refresh_parked):
    """teslamate-abrp

    A slightly convoluted way of getting your vehicle data from TeslaMate to A Better Route Planner.
    
    Arguments can be provided as command-line arguments or as environment variables.
    """
    # Initialize config dictionary
    config = {}

    # Set up logging early for better diagnostics
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        format='%(asctime)s: [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=log_level
    )
    
    logging.debug("Starting TeslaMate MQTT to ABRP application")
    logging.debug("Checking for credentials from various sources")

    # Check for Docker secrets first for sensitive data
    docker_token = get_docker_secret('USER_TOKEN')
    if docker_token:
        user_token = docker_token
        logging.debug("Using USER_TOKEN from Docker secret")

    # Check for MQTT credentials in Docker secrets
    docker_username = get_docker_secret('MQTT_USERNAME')
    if docker_username:
        mqtt_username = docker_username
        logging.debug("Using MQTT_USERNAME from Docker secret")
        
    docker_password = get_docker_secret('MQTT_PASSWORD')
    if docker_password:
        mqtt_password = docker_password
        logging.debug("Using MQTT_PASSWORD from Docker secret")
        # Automatically enable auth if password is found in Docker secrets
        if not use_auth and mqtt_password:
            use_auth = True
            logging.debug("Automatically enabling MQTT authentication due to password from Docker secret")

    # Required arguments checks with better error messages
    if not mqtt_server:
        click.echo("Error: MQTT server address not supplied. Please supply through environment variables or CLI argument.")
        sys.exit(1)

    if not user_token:
        click.echo("Error: User token not supplied. Please generate it through ABRP and supply through environment variables or CLI argument.")
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
    
    # Handle authentication - always use credentials if provided
    config["MQTTUSERNAME"] = mqtt_username
    # Only use password if we have a username
    config["MQTTPASSWORD"] = mqtt_password if mqtt_username else None
    
    config["MQTTTLS"] = use_tls
    # The CLI flag (--verify-cert/--no-verify-cert) wins when given; otherwise
    # fall back to the env var, parsed fail-secure (invalid -> verification on).
    if verify_cert is None:
        verify_cert = parse_bool_env('MQTT_VERIFY_CERT', True)
    config["MQTT_VERIFY_CERT"] = verify_cert
    config["CARMODEL"] = car_model
    config["BASETOPIC"] = status_topic
    config["SKIPLOCATION"] = skip_location
    config["DEBUG"] = debug

    # Refresh rates (validated with fallback to defaults inside TeslaMateABRP)
    config["REFRESH_RATE_DRIVING"] = refresh_driving
    config["REFRESH_RATE_CHARGING"] = refresh_charging
    config["REFRESH_RATE_PARKED"] = refresh_parked

    # Optional ABRP application-key override (Docker secret or env var); falls
    # back to the shared default inside TeslaMateABRP. Not a per-user secret.
    config["APIKEY"] = get_docker_secret('ABRP_API_KEY') or os.environ.get('ABRP_API_KEY')
    if config["APIKEY"]:
        logging.debug("Using ABRP_API_KEY override (Docker secret or env var)")

    # Enhanced credential logging for troubleshooting
    if config["MQTTUSERNAME"]:
        if config["MQTTPASSWORD"]:
            logging.debug("MQTT authentication configured with username and password")
        else:
            logging.debug("MQTT authentication configured with username only (no password)")
    else:
        logging.debug("No MQTT authentication credentials provided")
        
    if config["MQTTTLS"]:
        logging.debug(f"TLS enabled with certificate verification: {config['MQTT_VERIFY_CERT']}")

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