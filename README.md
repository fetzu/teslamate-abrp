# TeslaMate: MQTT to ABRP

[![codecov](https://codecov.io/gh/fetzu/teslamate-abrp/graph/badge.svg?token=5PBYBMOAEN)](https://codecov.io/gh/fetzu/teslamate-abrp)
[![Test and Build](https://github.com/fetzu/teslamate-abrp/actions/workflows/build_latest.yml/badge.svg)](https://github.com/fetzu/teslamate-abrp/actions/workflows/build_latest.yml)
[![GitHub release](https://img.shields.io/github/v/release/fetzu/teslamate-abrp)](https://github.com/fetzu/teslamate-abrp/releases/latest)
[![Docker Image Size](https://img.shields.io/docker/image-size/fetzu/teslamate-abrp/latest)](https://hub.docker.com/r/fetzu/teslamate-abrp)
[![Docker Pulls](https://img.shields.io/docker/pulls/fetzu/teslamate-abrp?color=%23099cec)](https://hub.docker.com/r/fetzu/teslamate-abrp)
[![GitHub license](https://img.shields.io/github/license/fetzu/teslamate-abrp)](https://github.com/fetzu/teslamate-abrp/blob/main/LICENSE)
  
A bridge to send your Tesla vehicle data from [TeslaMate](https://github.com/teslamate-org/teslamate) to [A Better Route Planner (ABRP)](https://abetterrouteplanner.com/).

## Features

- Automatically sends TeslaMate data to ABRP via their API
- Variable update rates based on vehicle state (driving, charging, parked)
- Support for multiple car models (Model S, 3, X, Y)
- Secure options for authentication and TLS encryption
- Support for Docker secrets management
- Optional location anonymization

## Setup Guide

### Prerequisites

- A working TeslaMate instance with MQTT enabled
- An ABRP user token
- Docker (recommended) or Python 3.x environment

### Getting an ABRP User Token

1. Log in to the ABRP web app or mobile app
2. Navigate to your car settings
3. Use the "generic" card (last one at the bottom) to generate your user token
4. Save this token securely - you'll need it to configure the bridge

### Option 1: Docker Setup (Recommended)

Add the teslamate-abrp service to your existing TeslaMate `docker-compose.yml`:

```yaml
ABRP:
  container_name: TeslaMate_ABRP
  image: fetzu/teslamate-abrp:latest
  restart: always
  environment:
    - MQTT_SERVER=mosquitto
    - USER_TOKEN=your-abrp-user-token
    - CAR_NUMBER=1
    # Optional parameters (see Configuration section)
    # - CAR_MODEL=tesla:m3:20:bt37:heatpump
    # - MQTT_USERNAME=username
    # - MQTT_PASSWORD=password
    # - MQTT_PORT=1883
    # - MQTT_TLS=True
    # - STATUS_TOPIC=teslamate-abrp
    # - SKIP_LOCATION=True
    # - TM2ABRP_DEBUG=True
```

Deploy the service:

```bash
docker-compose pull ABRP
docker-compose up -d ABRP
```

### Option 2: Using Docker with Secrets (More Secure)

For improved security, use Docker secrets to manage sensitive information:

```yaml
version: '3'
services:
  ABRP:
    container_name: TeslaMate_ABRP
    image: fetzu/teslamate-abrp:latest
    restart: always
    environment:
      - MQTT_SERVER=mosquitto
      - CAR_NUMBER=1
      - MQTT_USERNAME=username
      - MQTT_TLS=True
      - MQTT_PORT=8883
    secrets:
      - USER_TOKEN
      - MQTT_PASSWORD

secrets:
  USER_TOKEN:
    file: ./path/to/abrp-token.txt
  MQTT_PASSWORD:
    file: ./path/to/mqtt-password.txt
```

### Option 3: Running as a Python Script

1. Clone the repository:
   ```bash
   git clone https://github.com/fetzu/teslamate-abrp.git
   cd teslamate-abrp
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the script:
   ```bash
   python teslamate_mqtt2abrp.py USER_TOKEN 1 mqtt-server-address
   ```

## Configuration

### Essential Parameters

| Parameter | Description | Default | Required |
|-----------|-------------|---------|----------|
| USER_TOKEN | Your ABRP user token | - | Yes |
| CAR_NUMBER | TeslaMate car number | 1 | No |
| MQTT_SERVER | MQTT server address | - | Yes |

### Optional Parameters

| Parameter | Description | Default | Required |
|-----------|-------------|---------|----------|
| CAR_MODEL | ABRP car model identifier | Auto-detected | No |
| MQTT_PORT | MQTT server port | 1883 | No |
| MQTT_USERNAME | MQTT username | - | No |
| MQTT_PASSWORD | MQTT password | - | No |
| MQTT_TLS | Use TLS for MQTT connection | False | No |
| STATUS_TOPIC | Topic to publish status messages | - | No |
| SKIP_LOCATION | Don't send location data to ABRP | False | No |
| TM2ABRP_DEBUG | Enable debug logging | False | No |

### Car Model Identification

For optimal route planning, it's recommended to manually specify your car model using the `CAR_MODEL` parameter. Get the correct identifier from:
```
https://api.iternio.com/1/tlm/get_carmodels_list
```

Examples:
- Tesla Model 3 Long Range with Heat Pump: `tesla:m3:20:bt37:heatpump`
- Tesla Model Y Performance: `tesla:my:19:bt37:perf`
- Tesla Model S 100D: `s100d`

## Troubleshooting

1. Check logs for connection issues:
   ```bash
   docker-compose logs ABRP
   ```

2. Verify MQTT connectivity:
   ```bash
   docker-compose exec ABRP python -c "import paho.mqtt.client as mqtt; client = mqtt.Client(); client.connect('mosquitto', 1883); print('Connected successfully')"
   ```

3. Common issues:
   - MQTT server not reachable
   - Incorrect MQTT credentials
   - Invalid ABRP token
   - Wrong TeslaMate car number

## Advanced Usage

### Customizing Update Frequencies

The application uses different update rates based on car state:
- Driving: Updates every 1 second
- Charging: Updates every 6 seconds
- Parked/Asleep: Updates every 30 seconds

These values can be customized by editing the constants at the top of the Python script.

## Credits

Based on [letienne's original code](https://github.com/letienne/teslamate-abrp), with improvements by various contributors (see [commit history](https://github.com/fetzu/teslamate-abrp/commits/main)).

## License

Licensed under the [MIT license](https://github.com/fetzu/teslamate-abrp/blob/main/LICENSE).
