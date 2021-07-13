import paho.mqtt.client as mqtt
import requests
import sys
import json
import datetime
import calendar
import os
from time import sleep

apikey = os.environ['API_KEY']
mqttserver = os.environ['MQTT_SERVER']
usertoken = os.environ['USER_TOKEN']
carnumber = os.environ['CAR_NUMBER']

state = ""# car state
prev_state = ""# car state previous loop for tracking
data = {# dictionary of values sent to ABRP API
  "utc": "",
  "soc": "",
  "power": "",
  "speed": "",
  "lat": "",
  "lon": "",
  "elevation": "",
  "is_charging": "0",
  "is_dcfc": "0",
  "is_parked": "0",
  "battery_range": "",
  "ideal_battery_range": "",
  "ext_temp": "",
  "car_model":"s85d",
  "tlm_type": "api",
  "voltage": "",
  "current": "",
  "kwh_charged": "",
  "heading": "",
}


#initiate MQTT client
client = mqtt.Client("teslamateToABRP-4")
client.connect(mqttserver)

def on_connect(client, userdata, flags, rc):  # The callback for when the client connects to the broker
    print("Connected with result code {0}".format(str(rc)))  # Print result of connection attempt
    client.subscribe("teslamate/cars/4/#")
    #client.subscribe("digitest/test1")  # Subscribe to the topic “digitest/test1”, receive any messages published on it

#process MQTT messages
def on_message(client, userdata, message):
    global data
    global state
    try:
        #extracts message data from the received message
        payload = str(message.payload.decode("utf-8"))

        #updates the received data
        match message.topic:
            case "teslamate/cars/4/plugged_in":
                a=1#noop
            case "teslamate/cars/4/latitude":
                data["lat"] = payload
            case "teslamate/cars/4/longitude":
                data["lon"] = payload
            case "teslamate/cars/4/elevation":
                data["elevation"] = payload
            case "teslamate/cars/4/speed":
                data["speed"] = payload
            case "teslamate/cars/4/power":
                data["power"] = payload
                if(data["is_charging"]=="1" and int(payload)<-22):
                    data["is_dcfc"]="1"
            case "teslamate/cars/4/charger_power":
                if(payload!='' and int(payload)!=0):
                    data["is_charging"]="1"
                    if(int(payload)>22):
                        data["is_dcfc"]="1"
            case "teslamate/cars/4/heading":
                data["heading"] = payload
            case "teslamate/cars/4/outside_temp":
                data["ext_temp"] = payload
            case "teslamate/cars/4/odometer":
                data["odometer"] = payload
            case "teslamate/cars/4/ideal_battery_range_km":
                data["ideal_battery_range"] = payload
            case "teslamate/cars/4/est_battery_range_km":
                data["battery_range"] = payload
            case "teslamate/cars/4/charger_actual_current":
                if(payload!='' and int(payload) > 0):#charging
                    data["current"] = payload
                else:
                    del data["current"]
            case "teslamate/cars/4/charger_voltage":
                if(payload!='' and int(payload) > 0):
                    data["voltage"] = payload
                else:
                    del data["voltage"]
            case "teslamate/cars/4/shift_state":
                if(payload == "P"):
                    data["is_parked"]="1"
                elif(payload == "D" or payload == "R"):
                    data["is_parked"]="0"
            case "teslamate/cars/4/state":
                state = payload
                if(payload=="driving"):
                    data["is_parked"]="0"
                    data["is_charging"]="0"
                    data["is_dcfc"]="0"
                elif(payload=="charging"):
                    data["is_parked"]="1"
                    data["is_charging"]="1"
                    data["is_dcfc"]="0"
                elif(payload=="supercharging"):
                    data["is_parked"]="1"
                    data["is_charging"]="1"
                    data["is_dcfc"]="1"
                elif(payload=="online" or payload=="suspended" or payload=="asleep"):
                    data["is_parked"]="1"
                    data["is_charging"]="0"
                    data["is_dcfc"]="0"
            case "teslamate/cars/4/battery_level":
                data["soc"] = payload
            case "teslamate/cars/4/charge_energy_added":
                data["kwh_charged"] = payload
            case "teslamate/cars/4/inside_temp":
                a=0#noop
            case "teslamate/cars/4/since":
                a=0#noop
            case _:
                print("Unneeded topic:", message.topic, payload)
        return

    except:
        print("unexpected exception while processing message:", sys.exc_info()[0], message.topic, message.payload)

#starts the MQTT loop processing messages
client.on_message = on_message
client.on_connect = on_connect  # Define callback function for successful connection
client.loop_start()

#function to send data to ABRP
def updateABRP():
    global data
    global apikey
    global usertoken
    try:
        headers = {"Authorization": "APIKEY "+apikey}
        body = {"tlm": data}
        requests.post("https://api.iternio.com/1/tlm/send?token="+usertoken, headers=headers, json=body)
    except:
        print("unexpected exception while calling ABRP API:", sys.exc_info()[0])
        print(message.topic)
        print(message.payload)

#starts the forever loop updating ABRP
i = -1
while True:
    i+=1
    sleep(5)#refresh rate of min 5 seconds
    print(state)
    if state != prev_state:
        i = 120
    current_datetime = datetime.datetime.utcnow()
    current_timetuple = current_datetime.utctimetuple()
    data["utc"] = calendar.timegm(current_timetuple)#utc timestamp must be in every messafge
    if(state == "parked" or state == "online" or state == "suspended" or state=="asleep"):#if parked update every 10min
        if(i%120==0 or i>120):
            print("parked, updating every 10min")
            print(data)
            updateABRP()
            i = 0
    elif(state == "charging"):
        if(i%6==0):
            print("charging, updating every 30s")
            print(data)
            updateABRP()
    elif(state == "driving"):
        print("driving, updating every 5s")
        print(data)
        updateABRP()
    else:
        print("unknown state, not updating abrp")
        print(state)
    prev_state = state

client.loop_stop()