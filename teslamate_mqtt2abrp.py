## [ CLI with docopt ]
"""TeslaMate MQTT to ABRP

Usage: 
    teslamate_mqtt2abrp.py [-hlap] [USER_TOKEN] [CAR_NUMBER] [MQTT_SERVER] [MQTT_USERNAME] [MQTT_PASSWORD] [--model CAR_MODEL]

Arguments:
    USER_TOKEN          User token generated by ABRP.
    CAR_NUMBER          Car number from TeslaMate (usually 1).
    MQTT_SERVER         MQTT server address (e.g. "192.168.1.1").
    MQTT_USERNAME       MQTT username (e.g. "teslamate") - use with -l or -a.
    MQTT_PASSWORD       MQTT password (e.g. "etamalset") - use with -a.

Options:
    -h                  Show this screen.
    -l                  Use username to connect to MQTT server.
    -a                  Use authentification (user and password) to connect to MQTT server.
    --model CAR_MODEL   Car model according to https://api.iternio.com/1/tlm/get_CARMODELs_list

Note:
    All arguments can also be passed as corresponding OS environment variables.
"""

## [ IMPORTS ]
import sys
import datetime
import calendar
import os
import paho.mqtt.client as mqtt
import requests
import json
from time import sleep
from docopt import docopt

# Needed to intitialize docopt (for CLI)
if __name__ == '__main__':
    arguments = docopt(__doc__)

## [ CONFIGURATION ]
APIKEY = "d49234a5-2553-454e-8321-3fe30b49aa64"
MQTTUSERNAME = None
MQTTPASSWORD = None

if (arguments['-l'] is True or arguments['-a'] is True) and arguments['MQTT_USERNAME'] is not None: 
    MQTTUSERNAME = arguments['MQTT_USERNAME']
elif 'MQTT_USERNAME' in os.environ: MQTTUSERNAME = os.environ['MQTT_USERNAME']

if arguments['-a'] is True and arguments['MQTT_PASSWORD'] is not None:
    MQTTPASSWORD = arguments['MQTT_PASSWORD']
elif 'MQTT_PASSWORD' in os.environ: MQTTPASSWORD = os.environ['MQTT_PASSWORD']

if arguments['MQTT_SERVER'] is not None: MQTTSERVER = arguments['MQTT_SERVER']
elif 'MQTT_SERVER' in os.environ: MQTTSERVER = os.environ['MQTT_SERVER']
else: 
    sys.exit("MQTT server address not supplied. Please supply through ENV variables or CLI argument.")

if arguments['USER_TOKEN'] is not None: USERTOKEN = arguments['USER_TOKEN']
elif 'USER_TOKEN' in os.environ: USERTOKEN = os.environ['USER_TOKEN']
else: 
    sys.exit("User token not supplied. Please generate it through ABRP and supply through ENV variables or CLI argument.")

if arguments['CAR_NUMBER'] is not None: CARNUMBER = arguments['CAR_NUMBER']
elif 'CAR_NUMBER' in os.environ: CARNUMBER = os.environ['CAR_NUMBER']
else:
    CARNUMBER = 1
    print("Car number not supplied, defaulting to 1.")

print(arguments)
if arguments['--model'] is None: 
    if "CAR_MODEL" in os.environ: CARMODEL = os.environ["CAR_MODEL"]
    else: CARMODEL = None
else: CARMODEL = arguments['--model']

## [ VARS ]
state = "" #car state
prev_state = "" #car state previous loop for tracking
charger_phases = 1
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
        client.username_pw_set(MQTTUSERNAME, MQTTPASSWORD)
    else:
        client.username_pw_set(MQTTUSERNAME)

client.connect(MQTTSERVER)

def on_connect(client, userdata, flags, rc):  # The callback for when the client connects to the broker
    # MQTT Error handling
    if rc == 0: print("Connected with result code {0}. Connection with MQTT server established.".format(str(rc)))
    elif rc == 1: sys.exit("Connection to MQTT server refused: invalid protocol version.")
    elif rc == 2: sys.exit("Connection to MQTT server refused: invalid client identifier.")
    elif rc == 3: sys.exit("Connection to MQTT server refused: server unavailable.")
    elif rc == 4: sys.exit("Connection to MQTT server refused: bad username or password. Check your credentials.")
    elif rc == 5: sys.exit("Connection to MQTT server refused: not authorised. Provide username and password as needed.")
    elif rc >= 6 and rc <= 255: sys.exit("Connection to MQTT server refused: unknown reason. Seems like you are really unlucky today :(.")
    client.subscribe(f"teslamate/cars/{CARNUMBER}/#")

# Process MQTT messages
def on_message(client, userdata, message):
    global data
    global state
    global charger_phases
    try:
        #extracts message data from the received message
        payload = str(message.payload.decode("utf-8"))

        #updates the received data
        topic_postfix = message.topic.split('/')[-1]

        if topic_postfix == "plugged_in":
            a=1#noop
        elif topic_postfix == "model":
            data["model"] = payload
        elif topic_postfix == "trim_badging":
            data["trim_badging"] = payload
        elif topic_postfix == "latitude":
            data["lat"] = float(payload)
        elif topic_postfix == "longitude":
            data["lon"] = float(payload)
        elif topic_postfix == "elevation":
            data["elevation"] = int(payload)
        elif topic_postfix == "speed":
            data["speed"] = int(payload)
        elif topic_postfix == "power":
            data["power"] = float(payload)
            if(data["is_charging"]==True and int(payload)<-11):
                data["is_dcfc"]=True
        elif topic_postfix == "charger_power":
            if(payload!='' and int(payload)!=0):
                data["is_charging"]=True
                if int(payload)>11:
                    data["is_dcfc"]=True
        elif topic_postfix == "heading":
            data["heading"] = int(payload)
        elif topic_postfix == "outside_temp":
            data["ext_temp"] = float(payload)
        elif topic_postfix == "odometer":
            data["odometer"] = float(payload)
        elif topic_postfix == "ideal_battery_range_km":
            data["ideal_battery_range"] = float(payload)
        elif topic_postfix == "est_battery_range_km":
            data["est_battery_range"] = float(payload)
        elif topic_postfix == "charger_actual_current":
            if(payload!='' and int(payload) > 0): #charging, include current in message
                data["current"] = int(payload)
            else:
                data["current"] = 0
                del data["current"]
        elif topic_postfix == "charger_voltage":
            if(payload!='' and int(payload) > 5): #charging, include voltage in message
                data["voltage"] = int(payload)
            else:
                data["voltage"] = 0
                del data["voltage"]
        elif topic_postfix == "shift_state":
            if payload == "P":
                data["is_parked"]=True
            elif(payload == "D" or payload == "R"):
                data["is_parked"]=False
        elif topic_postfix == "state":
            state = payload
            if payload=="driving":
                data["is_parked"]=False
                data["is_charging"]=False
                data["is_dcfc"]=False
            elif payload=="charging":
                data["is_parked"]=True
                data["is_charging"]=True
                data["is_dcfc"]=False
            elif payload=="supercharging":
                data["is_parked"]=True
                data["is_charging"]=True
                data["is_dcfc"]=True
            elif(payload=="online" or payload=="suspended" or payload=="asleep"):
                data["is_parked"]=True
                data["is_charging"]=False
                data["is_dcfc"]=False
        elif topic_postfix == "usable_battery_level": #State of Charge of the vehicle (what's displayed on the dashboard of the vehicle is preferred)
            data["soc"] = int(payload)
        elif topic_postfix == "charge_energy_added":
            data["kwh_charged"] = float(payload)
        elif topic_postfix == "charger_phases":
            charger_phases = 3 if int(payload) > 1 else 1
        elif topic_postfix == "inside_temp":
            a=0 #Volontarely ignored
        elif topic_postfix == "since":
            a=0 #Volontarely ignored
        else:
            pass
            #print("Unneeded topic:", message.topic, payload)

        # Calculate acurrate power on AC charging
        if data["is_charging"]==True and data["is_dcfc"]==False and "voltage" in data and "current" in data:
            data["power"] = float(data["current"] * data["voltage"] * charger_phases) / 1000.0 * -1

        return

    except:
        print("unexpected exception while processing message:", sys.exc_info()[0], message.topic, message.payload)

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
            print("Your Model 3 trim could not be automatically determined. Trim reported as: "+data["trim_badging"])
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
            print("Your Model Y trim could not be automatically determined. Trim reported as: "+data["trim_badging"])
            return

    # Handle simple cases (aka Model S and Model X)
    else: data["car_model"] = data["model"].lower()+""+data["trim_badging"].lower()

    # Log the determined car model to the console
    if data["car_model"] is not None: print("Car model automatically determined as: "+data["car_model"])
    else: print("Car model could not be automatically determined, please set it through the CLI or environment var according to the documentation for best results.")

# If the car model is not yet known, find it
if CARMODEL is None: findCarModel()
else: print("Car model manually set to: "+CARMODEL)

## [ ABRP ]
# Function to send data to ABRP
def updateABRP():
    global data
    global APIKEY
    global USERTOKEN

    msgDetails = "Data object to send:"
    print(msgDetails, data)

    try:
        headers = {"Authorization": "APIKEY "+APIKEY}
        body = {"tlm": data}
        response = requests.post("https://api.iternio.com/1/tlm/send?token="+USERTOKEN, headers=headers, json=body)
        resp = response.json()
        if resp["status"] != "ok":
            print("Response from ABRP:", response.text)
        else:
            print("Response from ABRP:", resp["status"])
    except Exception as ex:
        print("Unexpected exception while calling ABRP API:", sys.exc_info()[0])
        print(ex)
    
## [ MAIN ]
# Starts the forever loop updating ABRP
i = -1
while True:
    i+=1
    sleep(1) #refresh rate of 1 cycle per second
    #print(state)
    if state != prev_state:
        i = 30
    current_datetime = datetime.datetime.now(datetime.UTC)
    current_timetuple = current_datetime.timetuple()
    data["utc"] = calendar.timegm(current_timetuple) #utc timestamp must be in every message
    str_now = current_datetime.strftime("%Y-%m-%d %H:%M:%S")
    msg = str_now + ": Car is " + state
    if(state == "parked" or state == "online" or state == "suspended" or state=="asleep" or state=="offline"): #if parked, update every 30 cylces/seconds
        if data["power"] != 0: #Sometimes after charging it keeps the last Power and not refresh any more until new drive or charge. 
            data["power"] = 0.0
        if "kwh_charged" in data:
            del data["kwh_charged"]
        if(i%30==0 or i>30):
            print(msg + ", updating every 30s.")
            updateABRP()
            i = 0
    elif state == "charging": #if charging, update every 6 cycles/seconds
        if i%6==0:
            print(msg +", updating every 6s.")
            updateABRP()
    elif state == "driving": #if driving, update every cycle/second
        print(msg + ", updating every second.")
        updateABRP()
    else:
        print(msg + " (unknown state), not sending any update to ABRP.")
    prev_state = state

client.loop_stop()
