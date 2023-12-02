## [ CLI with docopt ]
"""TeslaMate MQTT to ABRP

Usage: 
    teslamate_mqtt2abrp.py [-hdlpsx] [USER_TOKEN] [CAR_NUMBER] [MQTT_SERVER] [MQTT_USERNAME] [MQTT_PASSWORD] [MQTT_PORT] [--model CAR_MODEL] [--status_topic TOPIC]

Arguments:
    USER_TOKEN            User token generated by ABRP.
    CAR_NUMBER            Car number from TeslaMate (usually 1).
    MQTT_SERVER           MQTT server address (e.g. "192.168.1.1").
    MQTT_PORT             MQTT port (e.g. 1883 or 8883 for TLS).
    MQTT_USERNAME         MQTT username, use with -l or -p.
    MQTT_PASSWORD         MQTT password, use with -p.

Options:
    -h                    Show this screen.
    -d                    Debug mode (set logging level to DEBUG)
    -l                    Use username to connect to MQTT server.
    -p                    Use authentication (user and password) to connect to MQTT server.
    -s                    Use TLS to connect to MQTT server, environment variable: MQTT_TLS
    -x                    Don't send LAT and LON to ABRP, environment variable: SKIP_LOCATION
    --model CAR_MODEL     Car model according to https://api.iternio.com/1/tlm/get_CARMODELs_list
    --status_topic TOPIC  MQTT topic to publish status messages to, if not set, no publish will be done.

Note:
    All arguments can also be passed as corresponding OS environment variables.
"""

## [ IMPORTS ]
import sys
import datetime
import calendar
import os
import logging
import requests
import paho.mqtt.client as mqtt
from time import sleep
from docopt import docopt

# Needed to initialize docopt (for CLI)
if __name__ == '__main__':
    arguments = docopt(__doc__)

## [ CONFIGURATION ]
APIKEY = "d49234a5-2553-454e-8321-3fe30b49aa64"
MQTTUSERNAME = None
MQTTPASSWORD = None

## [ HELPER FUNCTIONS ]
def getDockerSecret(secretName):
    file = "/run/secrets/"+secretName
    if os.path.isfile(file):
        fo = open(file,"r")
        sec = fo.read().splitlines()[0]
        if len(sec) > 0:
            return sec
        else:
            return None
    else:
        return None

def publish_to_mqtt(dataObject):
    logging.debug("Publishing to MQTT: {}".format(dataObject))
    for key, value in dataObject.items():
        client.publish(
            "{}/{}".format(BASETOPIC, key),
            payload=value,
            qos=1,
            retain=True
        ) 

def niceNow():
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")

## [ PARAMETERS ]
if arguments['-d'] is True or 'TM2ABRP_DEBUG' in os.environ: 
    log_level = logging.DEBUG
else: log_level = logging.INFO
logging.basicConfig(format='%(asctime)s: [%(levelname)s] %(message)s', 
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=log_level)
logging.debug("Logging level set to DEBUG.")

if (arguments['-l'] is True or arguments['-a'] is True) and arguments['MQTT_USERNAME'] is not None: 
    MQTTUSERNAME = arguments['MQTT_USERNAME']
elif 'MQTT_USERNAME' in os.environ: MQTTUSERNAME = os.environ['MQTT_USERNAME']

if arguments['-p'] is True and arguments['MQTT_PASSWORD'] is not None:
    MQTTPASSWORD = arguments['MQTT_PASSWORD']
elif 'MQTT_PASSWORD' in os.environ: MQTTPASSWORD = os.environ['MQTT_PASSWORD']
elif getDockerSecret("MQTT_PASSWORD") is not None: MQTTPASSWORD = getDockerSecret("MQTT_PASSWORD")

if arguments['MQTT_SERVER'] is not None: MQTTSERVER = arguments['MQTT_SERVER']
elif 'MQTT_SERVER' in os.environ: MQTTSERVER = os.environ['MQTT_SERVER']
else: 
    sys.exit("MQTT server address not supplied. Please supply through ENV variables or CLI argument.")

if arguments['MQTT_PORT'] is not None: MQTTPORT = int(arguments['MQTT_PORT'])
elif 'MQTT_PORT' in os.environ: MQTTPORT = int(os.environ['MQTT_PORT'])
else: 
    MQTTPORT = 1883

if arguments['-s'] is True or 'MQTT_TLS' in os.environ:
    MQTTTLS = True
else:
    MQTTTLS = False

if arguments['USER_TOKEN'] is not None: USERTOKEN = arguments['USER_TOKEN']
elif 'USER_TOKEN' in os.environ: USERTOKEN = os.environ['USER_TOKEN']
elif getDockerSecret('USER_TOKEN') is not None: USERTOKEN = getDockerSecret('USER_TOKEN')
else: 
    sys.exit("User token not supplied. Please generate it through ABRP and supply through ENV variables or CLI argument.")

if arguments['CAR_NUMBER'] is not None: CARNUMBER = arguments['CAR_NUMBER']
elif 'CAR_NUMBER' in os.environ: CARNUMBER = os.environ['CAR_NUMBER']
else:
    CARNUMBER = 1
    logging.info("Car number not supplied, defaulting to 1.")

logging.debug("Arguments passed: {}".format(arguments))
if arguments['--model'] is None: 
    if "CAR_MODEL" in os.environ: CARMODEL = os.environ["CAR_MODEL"]
    else: CARMODEL = None
else: CARMODEL = arguments['--model']

# Log to MQTT topic if specified
if arguments['--status_topic'] is not None: BASETOPIC = arguments['--status_topic']
elif 'STATUS_TOPIC' in os.environ: BASETOPIC = os.environ['STATUS_TOPIC']
else: BASETOPIC = None

# Skip LAT and LON if specified
if arguments['-x'] is True or 'SKIP_LOCATION' in os.environ: SKIPLOCATION = True
else: SKIPLOCATION = False

## [ VARS ]
state = "" #car state
prev_state = "" #car state previous loop for tracking
charger_phases = 1
prefix = "_tm2abrp"
if BASETOPIC is not None: state_topic = BASETOPIC + "/" + prefix + "_status" # MQTT topic to publish status messages to
data = { #dictionary of values sent to ABRP API
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
    "car_model":f"{CARMODEL}",
    "tlm_type": "api",
    "voltage": 0,
    "current": 0,
    "kwh_charged": 0,
    "heading": 0
}

## [ MQTT ]
# Initialize MQTT client and connect
client = mqtt.Client(f"teslamateToABRP-{CARNUMBER}")
if MQTTUSERNAME is not None:
    if MQTTPASSWORD is not None:
        logging.debug("Using MQTT username: {} and password '******'".format(MQTTUSERNAME))
        client.username_pw_set(MQTTUSERNAME, MQTTPASSWORD)
    else:
        logging.debug("Using MQTT username: {}".format(MQTTUSERNAME))
        client.username_pw_set(MQTTUSERNAME)
if MQTTTLS is True:
    logging.debug("Using TLS with MQTT")
    client.tls_set()
if BASETOPIC is not None:
    logging.debug("Using MQTT base topic: {} for last will".format(BASETOPIC))
    client.will_set(state_topic, payload="offline", qos=2, retain=True)
logging.debug("Trying to connect to {}:{}".format(MQTTSERVER,MQTTPORT))
client.connect(MQTTSERVER, MQTTPORT)

def on_connect(client, userdata, flags, rc):  # The callback for when the client connects to the broker
    # MQTT Error handling
    logging.info("MQTT Connection returned result: {} Code {}.".format(mqtt.connack_string(rc),rc))
    if rc != 0:
        sys.exit("Could not connect")
    client.subscribe(f"teslamate/cars/{CARNUMBER}/#")
    if BASETOPIC is not None: client.publish(state_topic, payload="online", qos=2, retain=True)

# Process MQTT messages
def on_message(client, userdata, message):
    global data
    global state
    global charger_phases
    try:
        #extracts message data from the received message
        payload = str(message.payload.decode("utf-8"))

        match (message.topic.split('/')[-1]):
            case "model":
                data["model"] = payload
            case "trim_badging":
                data["trim_badging"] = payload
            case "latitude":
                if not SKIPLOCATION:
                    data["lat"] = float(payload)
            case "longitude":
                if not SKIPLOCATION:
                    data["lon"] = float(payload)
            case "elevation":
                data["elevation"] = int(payload)
            case "speed":
                data["speed"] = int(payload)
            case "power":
                data["power"] = float(payload)
                if(data["is_charging"] == True and int(payload)<-11):
                    data["is_dcfc"] = True
            case "charger_power":
                if(payload != '' and int(payload)!=0):
                    data["is_charging"] = True
                    if int(payload)>11:
                        data["is_dcfc"] = True
            case "heading":
                data["heading"] = int(payload)
            case "outside_temp":
                data["ext_temp"] = float(payload)
            case "odometer":
                data["odometer"] = float(payload)
            case "ideal_battery_range_km":
                data["ideal_battery_range"] = float(payload)
            case "est_battery_range_km":
                data["est_battery_range"] = float(payload)
            case "charger_actual_current":
                if(payload != '' and int(payload) > 0): #charging, include current in message
                    data["current"] = int(payload)
                else:
                    data["current"] = 0
                    del data["current"]
            case "charger_voltage":
                if(payload != '' and int(payload) > 5): #charging, include voltage in message
                    data["voltage"] = int(payload)
                else:
                    data["voltage"] = 0
                    del data["voltage"]
            case "shift_state":
                if payload == "P":
                    data["is_parked"] = True
                elif payload in ["D","R","N"]:
                    data["is_parked"] = False
            case "state":
                state = payload
                if payload == "driving":
                    data["is_parked"] = False
                    data["is_charging"] = False
                    data["is_dcfc"] = False
                elif payload == "charging":
                    data["is_parked"] = True
                    data["is_charging"] = True
                    data["is_dcfc"] = False
                elif payload == "supercharging":
                    data["is_parked"] = True
                    data["is_charging"] = True
                    data["is_dcfc"] = True
                elif payload in ["online", "suspended", "asleep"]:
                    data["is_parked"] = True
                    data["is_charging"] = False
                    data["is_dcfc"] = False
            case "usable_battery_level": #State of Charge of the vehicle (what's displayed on the dashboard of the vehicle is preferred)
                data["soc"] = int(payload)
            case "charge_energy_added":
                data["kwh_charged"] = float(payload)
            case "charger_phases":
                charger_phases = 3 if payload and int(payload) > 1 else 1 #Fixes processing error when transitioning out of charging
            case _:
                # Unhandled
                logging.debug("Unneeded topic: {} {}".format(message.topic, payload))
                pass
            
        # Calculate accurate power on AC charging
        if data["is_charging"] == True and data["is_dcfc"] == False and "voltage" in data and "current" in data:
            data["power"] = float(data["current"] * data["voltage"] * charger_phases) / 1000.0 * -1

        return

    except:
        logging.critical("Unexpected exception while processing message: {} {} {}".format(sys.exc_info()[0], message.topic, message.payload))
    
# Starts the MQTT loop processing messages
client.on_message = on_message
client.on_connect = on_connect  # Define callback function for successful connection
client.loop_start()

## [ CAR MODEL ]
# Function to find out car model from TeslaMate data
def findCarModel():
    sleep(10) #sleep long enough to receive the first message

    # Handle model 3 cases
    if data["model"] == "3":
        if data["trim_badging"] == "50":
            data["car_model"] = "3standard"
        elif data["trim_badging"] == "62":
            data["car_model"] = "3mid"
        elif data["trim_badging"] == "74":
            data["car_model"] = "3long"
        elif data["trim_badging"] == "74D":
            data["car_model"] = "3long_awd"
        elif data["trim_badging"] == "P74D":
            data["car_model"] = "3p20"
        else:
            logging.warning("Your Model 3 trim could not be automatically determined. Trim reported as: {}.".format(data["trim_badging"]))
            return
    
    # Handle model Y cases
    if data["model"] == "Y":
        if data["trim_badging"] == "74D":
            data["car_model"] = "tesla:my:19:bt37:awd"
        elif data["trim_badging"] == "P74D":
            data["car_model"] = "tesla:my:19:bt37:perf"
        elif data["trim_badging"] == "50":
            data["car_model"] = "tesla:my:22:my_lfp:rwd"
        else:
            logging.warning("Your Model Y trim could not be automatically determined. Trim reported as: {}.".format(data["trim_badging"]))
            return

    # Handle simple cases (aka Model S and Model X)
    else: data["car_model"] = data["model"].lower()+""+data["trim_badging"].lower()

    # Log the determined car model to the console
    if data["car_model"] is not None: logging.info("Car model automatically determined as: {}.".format(data["car_model"]))
    else: logging.warning("Car model could not be automatically determined, please set it through the CLI or environment var according to the documentation for best results.")

# If the car model is not yet known, find it
if CARMODEL is None: findCarModel()
else: logging.info("Car model manually set to: {}.".format(CARMODEL))

## [ ABRP ]
# Function to send data to ABRP
def updateABRP():
    global data
    global APIKEY
    global USERTOKEN

    try:
        headers = {"Authorization": "APIKEY "+APIKEY}
        body = {"tlm": data}
        response = requests.post("https://api.iternio.com/1/tlm/send?token="+USERTOKEN, headers=headers, json=body)
        resp = response.json()
        if BASETOPIC is not None:
            publish_to_mqtt({"{}_post_last_status".format(prefix): resp["status"]})
        if resp["status"] != "ok":
            logging.error("Error, response from the ABRP API: {}.".format(response.text))
            if BASETOPIC is not None:
                publish_to_mqtt({"{}_post_last_error".format(prefix): niceNow()})
        else:
            logging.info("Data object successfully sent: {}".format(data))
            if BASETOPIC is not None:
                publish_to_mqtt({"{}_post_last_success".format(prefix): niceNow()})
    except Exception as ex:
        logging.critical("Unexpected exception while POSTing to ABRP API: {}".format(sys.exc_info()[0]))
        logging.debug("Error message from ABRP API POST request: {}".format(ex))
        if BASETOPIC is not None: 
            publish_to_mqtt({"{}_post_exception".format(prefix): ex})
            publish_to_mqtt({"{}_post_last_exception".format(prefix): niceNow()})

## [ MAIN ]
# Starts the forever loop updating ABRP
i = -1
while True:
    i+=1
    sleep(1) #refresh rate of 1 cycle per second
    if state != prev_state:
        i = 30
        logging.debug("Current car state changed to: {}.".format(state))
    current_datetime = datetime.datetime.now(datetime.UTC)
    current_timetuple = current_datetime.timetuple()
    data["utc"] = calendar.timegm(current_timetuple) #utc timestamp must be in every message
    if state in ["parked", "online", "suspended", "asleep", "offline"]: #if parked, update every 30 cycles/seconds
        if data["power"] != 0: #sometimes after charging the last power value is kept and not refreshed until the next drive or charge session. 
            data["power"] = 0.0
        if data["speed"] > 0: #sometimes after driving the last speed value is kept and not refreshed until the next drive or charge session. 
            data["speed"] = 0
        if "kwh_charged" in data:
            del data["kwh_charged"]
        if(i%30==0 or i>30):
            if prev_state != state: logging.info("Car is sleeping, updating every 30s.")
            updateABRP()
            if BASETOPIC is not None: publish_to_mqtt(data)
            i = 0
    elif state == "charging": #if charging, update every 6 cycles/seconds
        if i%6==0:
            if prev_state != state: logging.info("Car is charging, updating every 6s.")
            updateABRP()
            if BASETOPIC is not None: publish_to_mqtt(data)
    elif state == "driving": #if driving, update every cycle/second
        if prev_state != state: logging.info("Car is driving, updating every second.")
        updateABRP()
        if BASETOPIC is not None: publish_to_mqtt(data)
    else:
        logging.error("Car is in unknown state ({}), not sending any update to ABRP.".format(state))
    prev_state = state

client.loop_stop()
