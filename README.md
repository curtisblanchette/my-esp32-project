# My ESP32 Project

A monorepo for testing ESP32 devices. These embedded IoT devices collect telemetry data from a collection of differing sensors and devices. HOT data is stored in a Redis database and aggregated data is stored in a SQLite database.
The dashboard is a React SPA that displays both real-time sensor data and aggregated data for linear chart views.

## Preview

![img.png](img.png)

## Table of Contents
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [ESP32 Device Setup](#esp32-device-setup)
- [Infrastructure](#infrastructure)
- [Development](#development)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)

## Prerequisites

### Hardware
- **ESP32 Development Board** with USB connection

### Software
- **Docker** and **Docker Compose** - For running the infrastructure stack
- **Node.js** (v18+) and **npm** - For local development
- **Python 3** - For device management scripts
- **mpremote** - MicroPython remote utility for device interaction
  ```bash
  pip install mpremote
  ```

### MicroPython Binary
The project includes a pre-downloaded MicroPython binary for ESP32:
```
./bin/ESP32_GENERIC-20251209-v1.27.0.bin
```

## Quick Start

### 1. Start the Infrastructure
Launch all backend services using Docker Compose:
```bash
docker-compose up -d
```

This will start:
- **MQTT Broker** (Mosquitto) on port `1883`
- **Redis** on port `6381`
- **API Server** on port `3000`
- **Web Dashboard** on port `5173`

### 2. Access the Dashboard
Open your browser to:
```
http://localhost:5173
```

### 3. Configure and Flash ESP32 Device
See [ESP32 Device Setup](#esp32-device-setup) below.

## ESP32 Device Setup

### Initial Setup

#### 1. Connect ESP32
Connect your ESP32 device to your computer via USB.

#### 2. Flash MicroPython Firmware
Flash the device with the included MicroPython binary:
```bash
esptool.py --chip esp32 --port /dev/ttyUSB0 erase_flash
esptool.py --chip esp32 --port /dev/ttyUSB0 write_flash -z 0x1000 ./bin/ESP32_GENERIC-20251209-v1.27.0.bin
```

**Note:** Replace `/dev/ttyUSB0` with your actual device port:
- macOS: `/dev/tty.usbserial-*` or `/dev/tty.SLAB_USBtoUART`
- Linux: `/dev/ttyUSB0` or `/dev/ttyACM0`
- Windows: `COM3` or similar

#### 3. Configure Device Secrets
Copy the example secrets file and fill in your network credentials:
```bash
cp ./device/secrets_example.py ./device/secrets.py
```

Edit `./device/secrets.py` with your WiFi and API details:
```python
SSID = "Your_WiFi_SSID"
PASSWORD = "Your_WiFi_Password"
API_BASE_URL = "http://192.168.1.XXX:3000"  # Your computer's local IP
```

**Important:** Use your computer's local network IP address, not `localhost`, so the ESP32 can reach the API server.

#### 4. Upload Device Code
Upload all device files to the ESP32:
```bash
./tools/flash.sh
```

This script uses `mpremote` to copy all files from `./device/` to the ESP32's filesystem.

### Device Management

#### Monitor Serial Console
View real-time output from the ESP32:
```bash
./tools/repl.sh
```

Press `Ctrl+]` to exit the REPL.

#### Reset Device
Soft reset the ESP32 to restart the application:
```bash
./tools/reset.sh
```

#### Manual File Upload
To upload specific files manually:
```bash
mpremote connect /dev/ttyUSB0 cp ./device/main.py :main.py
```

## Infrastructure

The project uses Docker Compose to orchestrate multiple services:

### MQTT Message Broker (Mosquitto)
- **Purpose:** Handles ingestion of telemetry events from ESP32 devices
- **Port:** `1883`
- **Topics:** `/device/<device_id>/<metric>`
- **Configuration:** `./mosquitto/mosquitto.conf`

### Redis
- **Purpose:** HOT storage for real-time telemetry data (48-hour retention)
- **Port:** `6381` (mapped from container port `6379`)
- **Persistence:** Append-only file (AOF) enabled
- **Volume:** `redis_data`

### SQLite
- **Purpose:** COLD storage for aggregated telemetry data (30-day views)
- **Location:** `./data/telemetry.sqlite`
- **Access:** Mounted into API container
- **Journal Mode:** DELETE (for compatibility)

### API Service
- **Purpose:** REST API for querying telemetry data and managing device state
- **Technology:** Node.js/TypeScript
- **Port:** `3000`
- **REST Endpoints:**
  - `GET /telemetry/recent` - Query recent data from Redis
  - `GET /telemetry/historical` - Query aggregated data from SQLite
- **WebSocket Endpoint:** `ws://localhost:3000/ws`
  - **Real-time Updates:** Broadcasts telemetry and relay state changes to all connected clients
  - **Connection:** Clients receive initial state immediately upon connection
  - **Message Types:**
    - `latest` - Latest sensor readings (temp, humidity, timestamp)
    - `relays` - Relay configuration and state updates
  - **Usage:** Dashboard uses WebSocket for live data updates without polling
- **Data Ingestion:** Telemetry data is ingested via MQTT broker, not REST API
- **Environment Variables:** See `docker-compose.yml`

### Web Dashboard
- **Purpose:** React SPA for visualizing telemetry data
- **Technology:** React + Vite + TypeScript
- **Port:** `5173`
- **Features:**
  - Real-time metric cards
  - Time-series charts (48hr and 30d views)
  - Relay control interface
  - Responsive design

## Development

### Local Development (without Docker)

#### Install Dependencies
```bash
npm install
```

#### Start All Services
Using Turborepo for parallel execution:
```bash
npm run dev
```

This runs both API and Web in development mode.

#### Individual Services
```bash
# API only
cd apps/api
npm run dev

# Web only
cd apps/web
npm run dev
```

### Build for Production
```bash
npm run build
```

### Type Checking
```bash
npm run typecheck
```

### Linting
```bash
npm run lint
```

### Working with Device Code

The `./device/` directory contains MicroPython code:

- **`boot.py`** - Runs on device startup, handles WiFi connection
- **`main.py`** - Main application loop, sensor reading and data publishing
- **`config.py`** - Device configuration settings
- **`secrets.py`** - WiFi and API credentials (gitignored)
- **`lib/`** - Hardware abstraction modules
  - `led.py` - LED control
  - `wifi.py` - WiFi management
  - `sensors/` - Sensor drivers
- **`services/`** - Service integrations
  - `mqtt.py` - MQTT client
  - `web.py` - HTTP client for API calls



## Project Structure
```
├── apps
│   ├── api
│   │   ├── Dockerfile
│   │   ├── data
│   │   ├── dist
│   │   ├── node_modules
│   │   ├── package.json
│   │   ├── src
│   │   └── tsconfig.json
│   └── web
│       ├── Dockerfile
│       ├── index.html
│       ├── node_modules
│       ├── package.json
│       ├── public
│       ├── src
│       ├── tsconfig.json
│       └── vite.config.ts
├── bin
│   └── ESP32_GENERIC-20251209-v1.27.0.bin
├── data
│   └── telemetry.sqlite
├── device
│   ├── boot.py
│   ├── config.py
│   ├── lib
│   │   ├── __init__.py
│   │   ├── led.py
│   │   ├── sensors
│   │   └── wifi.py
│   ├── main.py
│   ├── secrets.py
│   ├── secrets_example.py
│   └── services
│       ├── __init__.py
│       ├── mqtt.py
│       └── web.py
├── docker-compose.yml
├── mosquitto
│   └── mosquitto.conf
├── package-lock.json
├── package.json
├── requirements.txt
├── tools
│   ├── flash.sh
│   ├── repl.sh
│   ├── reset.sh
│   └── upload.py
└── turbo.json
```

## Troubleshooting

### ESP32 Device Issues

#### Device Not Connecting to WiFi
- Verify WiFi credentials in `./device/secrets.py`
- Check that your WiFi network is 2.4GHz (ESP32 doesn't support 5GHz)
- Monitor the serial console with `./tools/repl.sh` to see connection errors
- Ensure the ESP32 is within range of your WiFi router

#### Cannot Flash Device
- Check USB cable (some cables are power-only, need data cable)
- Verify correct port with `ls /dev/tty.*` (macOS) or `ls /dev/ttyUSB*` (Linux)
- Install USB-to-Serial drivers if needed (CP210x or CH340)
- Try holding the BOOT button while flashing

#### Device Keeps Rebooting
- Check power supply (USB port may not provide enough current)
- Look for errors in serial console output
- Verify all required files were uploaded correctly
- Check for syntax errors in device code

#### No Data Appearing in Dashboard
- Verify ESP32 is connected to WiFi (check serial output)
- Ensure `API_BASE_URL` in `secrets.py` uses your computer's local IP, not `localhost`
- Check that API server is running: `curl http://localhost:3000/health`
- Verify MQTT broker is accessible from ESP32
- Check API logs: `docker logs my-esp32-api`

#### TypeScript Errors
```bash
# Run type checking
npm run typecheck

# Check individual apps
cd apps/api && npm run typecheck
cd apps/web && npm run typecheck
```

#### Hot Reload Not Working
- Ensure volumes are correctly mounted in `docker-compose.yml`
- Try restarting the specific service: `docker-compose restart web`

#### Check Service Health
```bash
# API health check
curl http://localhost:3000/health

# Redis connection
redis-cli -p 6381 ping

# MQTT broker
mosquitto_pub -h localhost -p 1883 -t test -m "hello"
```

