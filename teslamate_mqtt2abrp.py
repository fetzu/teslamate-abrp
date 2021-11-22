## [ CLI with docopt ]
"""
TeslaMate MQTT to ABRP

  Usage: 
    teslamate_mqtt2abrp.py [-hlap] USER_TOKEN CAR_NUMBER MQTT_SERVER [MQTT_USERNAME] [MQTT_PASSWORD] [-m CAR_MODEL]
  
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
    -m                  Car model according to https://api.iternio.com/1/tlm/get_CARMODELs_list

  Note:
    All arguments can also be passed as corresponding OS environment variables.
"""

## [ IMPORTS ]
import sys
import datetime
import calendar
import os
from time import sleep
import paho.mqtt.client as mqtt
import requests
from docopt import docopt

# Needed to intitialize docopt (for CLI)
if __name__ == '__main__':
    arguments = docopt(__doc__)

## [ CONFIGURATION ]
APIKEY = "d49234a5-2553-454e-8321-3fe30b49aa64"
MQTTUSERNAME = None
MQTTPASSWORD = None

if arguments['-l'] is True:
    if arguments['MQTT_USERNAME'] is None: MQTTUSERNAME = os.environ['MQTT_USERNAME']
    else: MQTTUSERNAME = arguments['MQTT_USERNAME']

if arguments['-a'] is True:
    if arguments['MQTT_USERNAME'] is None: MQTTUSERNAME = os.environ['MQTT_USERNAME']
    if arguments['MQTT_PASSWORD'] is None: MQTTPASSWORD = os.environ['MQTT_PASSWORD']
    else: 
        MQTTUSERNAME = arguments['MQTT_USERNAME']
        MQTTPASSWORD = arguments['MQTT_PASSWORD']

if arguments['MQTT_SERVER'] is None: MQTTSERVER = os.environ['MQTT_SERVER']
else: MQTTSERVER = arguments['MQTT_SERVER']

if arguments['USER_TOKEN'] is None: USERTOKEN = os.environ['USER_TOKEN']
else: USERTOKEN = arguments['USER_TOKEN']

if arguments['CAR_NUMBER'] is None: CARNUMBER = os.environ['CAR_NUMBER']
else: CARNUMBER = arguments['CAR_NUMBER']

if arguments['CAR_MODEL'] is None: 
    if "CAR_MODEL" in os.environ: CARMODEL = os.environ["CAR_MODEL"]
    else: CARMODEL = None
else: CARMODEL = arguments['CAR_MODEL']


## [ VARS ]
state = "" #car state
prev_state = "" #car state previous loop for tracking
data = { #dictionary of values sent to ABRP API
  "utc": 0,
  "soc": 0,
  "power": 0,
  "speed": 0,
  "lat": "",
  "lon": "",
  "elevation": "",
  "is_charging": 0,
  "is_dcfc": 0,
  "is_parked": 0,
  "battery_range": "",
  "ideal_battery_range": "",
  "ext_temp": "",
  "model": "",
  "trim_badging": "",
  "car_model":f"{CARMODEL}",
  "tlm_type": "api",
  "voltage": 0,
  "current": 0,
  "kwh_charged": 0,
  "heading": "",
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
    print("Connected with result code {0}".format(str(rc)))  # Print result of connection attempt
    client.subscribe(f"teslamate/cars/{CARNUMBER}/#")

# Process MQTT messages
def on_message(client, userdata, message):
    global data
    global state
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
            data["lat"] = payload
        elif topic_postfix == "longitude":
            data["lon"] = payload
        elif topic_postfix == "elevation":
            data["elevation"] = payload
        elif topic_postfix == "speed":
            data["speed"] = int(payload)
        elif topic_postfix == "power":
            data["power"] = int(payload)
            if(data["is_charging"]==1 and int(payload)<-22):
                data["is_dcfc"]=1
        elif topic_postfix == "charger_power":
            if(payload!='' and int(payload)!=0):
                data["is_charging"]=1
                if int(payload)>22:
                    data["is_dcfc"]=1
        elif topic_postfix == "heading":
            data["heading"] = payload
        elif topic_postfix == "outside_temp":
            data["ext_temp"] = payload
        elif topic_postfix == "odometer":
            data["odometer"] = payload
        elif topic_postfix == "ideal_battery_range_km":
            data["ideal_battery_range"] = payload
        elif topic_postfix == "est_battery_range_km":
            data["battery_range"] = payload
        elif topic_postfix == "charger_actual_current":
            if(payload!='' and int(payload) > 0):#charging
                data["current"] = payload
            else:
                del data["current"]
        elif topic_postfix == "charger_voltage":
            if(payload!='' and int(payload) > 5):
                data["voltage"] = payload
            else:
                del data["voltage"]
        elif topic_postfix == "shift_state":
            if payload == "P":
                data["is_parked"]="1"
            elif(payload == "D" or payload == "R"):
                data["is_parked"]="0"
        elif topic_postfix == "state":
            state = payload
            if payload=="driving":
                data["is_parked"]=0
                data["is_charging"]=0
                data["is_dcfc"]=0
            elif payload=="charging":
                data["is_parked"]=1
                data["is_charging"]=1
                data["is_dcfc"]=0
            elif payload=="supercharging":
                data["is_parked"]=1
                data["is_charging"]=1
                data["is_dcfc"]=1
            elif(payload=="online" or payload=="suspended" or payload=="asleep"):
                data["is_parked"]=1
                data["is_charging"]=0
                data["is_dcfc"]=0
        elif topic_postfix == "battery_level":
            data["soc"] = payload
        elif topic_postfix == "charge_energy_added":
            data["kwh_charged"] = payload
        elif topic_postfix == "inside_temp":
            a=0#noop
        elif topic_postfix == "since":
            a=0#noop
        else:
            pass
            #print("Unneeded topic:", message.topic, payload)
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
        elif data["trim_badging"] == "74":
            data["car_model"] = "3long"
        elif data["trim_badging"] == "74D":
            data["car_model"] = "3long_awd"
        elif data["trim_badging"] == "P74D":
            data["car_model"] = "3p20"
    
    # TODO: Handle model Y cases
    if data["model"] == "Y":
        print("Unfortunately, Model Y is not supported yet and should be set through the CLI or environment var.")

    # Handle simple cases (aka Model S and Model X)
    else: data["car_model"] = data["model"]+""+data["trim_badging"]

    # Log the determined car model to the console
    print("Car model automatically determined as: "+data["car_model"])

# If the car model is not yet known, find it
if CARMODEL is None: findCarModel()

## [ ABRP ]
# Function to send data to ABRP
def updateABRP():
    global data
    global APIKEY
    global USERTOKEN
    try:
        headers = {"Authorization": "APIKEY "+APIKEY}
        body = {"tlm": data}
        requests.post("https://api.iternio.com/1/tlm/send?token="+USERTOKEN, headers=headers, json=body)
    except:
        print("Unexpected exception while calling ABRP API:", sys.exc_info()[0])
        print(message.topic)
        print(message.payload)

## [ MAIN ]

# Starts the forever loop updating ABRP
i = -1
while True:
    i+=1
    sleep(5)#refresh rate of min 5 seconds
    #print(state)
    if state != prev_state:
        i = 120
    current_datetime = datetime.datetime.utcnow()
    current_timetuple = current_datetime.utctimetuple()
    data["utc"] = calendar.timegm(current_timetuple)#utc timestamp must be in every message
    if(state == "parked" or state == "online" or state == "suspended" or state=="asleep" or state=="offline"):#if parked update every 10min
        if "kwh_charged" in data:
            del data["kwh_charged"]
        if(i%120==0 or i>120):
            print("parked, updating every 10min")
            print(data)
            updateABRP()
            i = 0
    elif state == "charging":
        if i%6==0:
            print("charging, updating every 30s")
            print(data)
            updateABRP()
    elif state == "driving":
        print("driving, updating every 5s")
        print(data)
        updateABRP()
    else:
        print("unknown state, not updating abrp")
        print(state)
    prev_state = state

client.loop_stop()
