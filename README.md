# My ESP32 Project
A monorepo for testing ESP32 devices. These embedded IoT devices collect telemetry data from a collection of differing sensors and devices. HOT data is stored in a Redis database and aggregated data is stored in a SQLite database.
The dashboard is a React SPA that displays the both real-time sensor data and aggregated data for linear chart views.

## Preview

![img.png](img.png)

## Requirements
Devices: **ESP32** flashed with MicroPython
- **MicroPython Binary**
- Binary to flash to ESP32 
```./bin/ESP32_GENERIC_20251209-v1.27.0.bin```

### **$ mpremote**
Micropython Remote utility to upload code to ESP32 and interact with a connected device.

### Testing Code on the ESP32
1. First, connect ESP32 over USB.
2. Flash the device with the MicoPython binary.
3. Fill environment variables in `./device/secrets.py`.
4. Upload the device files with `./tools/flash.sh`.
4. Monitor the serial console with `./tools/repl.sh`.
5. Reset the device with `./tools/reset.sh`.

### Upload Device Files
```bash
./tools/flash.sh
``` 
### Monitor Serial Console
```bash
./tools/repl.sh
```
### Reset Device
```bash
./tools/reset.sh
```

## Infrastructure

### --  Docker Compose Orchestration --

### MQTT Message Broker
Handles ingestion of telemetry events

### Redis
HOT storage of realtime telemetry data. Used to drive 48hr views.

### SQLite
COLD storage of aggregated telemetry data. Used to drive 30d views.

### Node App: API
REST API for ingesting telemetry events and querying telemetry data.

### Node App: Web
React SPA for visualizing telemetry data.



## Project Structure
This project uses a monorepo structure with a number of subprojects coordinating the different components of the system.

```
├── README.md
├── apps
│   ├── api
│   │   ├── Dockerfile
│   │   ├── data
│   │   ├── dist
│   │   ├── node_modules
│   │   ├── package.json
│   │   ├── src
│   │   └── tsconfig.json
│   └── web
│       ├── Dockerfile
│       ├── index.html
│       ├── node_modules
│       ├── package.json
│       ├── public
│       ├── src
│       ├── tsconfig.json
│       └── vite.config.ts
├── bin
│   └── ESP32_GENERIC-20251209-v1.27.0.bin
├── data
│   └── telemetry.sqlite
├── device
│   ├── boot.py
│   ├── config.py
│   ├── lib
│   │   ├── __init__.py
│   │   ├── led.py
│   │   ├── sensors
│   │   └── wifi.py
│   ├── main.py
│   ├── secrets.py
│   ├── secrets_example.py
│   └── services
│       ├── __init__.py
│       ├── mqtt.py
│       └── web.py
├── docker-compose.yml
├── img.png
├── mosquitto
│   └── mosquitto.conf
├── package-lock.json
├── package.json
├── requirements.txt
├── tools
│   ├── flash.sh
│   ├── repl.sh
│   ├── reset.sh
│   └── upload.py
└── turbo.json
```


