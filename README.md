# ESP32 Test Project

## Requirements
### **ESP32**

### **MicroPython Binary**
Binary to flash to ESP32 
```./bin/ESP32_GENERIC_20251209-v1.27.0.bin```

### **mpremote**
Used to upload code to ESP32 and monitor the Serial Console

### **Simple Web Server**
Serve a web service for testing api calls from the esp32
```python 
python -m http.server 3000
```

## Testing Code on the ESP32
1. First, connect ESP32 over USB.
2. Flash the device with the MicoPython binary.
3. Fill environment variables in `secrets.py`.
4. Upload the device files with `./tools/flash.sh`.
4. Monitor the serial console with `./tools/repl.sh`.
5. Reset the device with `./tools/reset.sh`.

### Flash (Upload Device Files)
```bash
./tools/flash.sh
``` 
### Monitor
```bash
./tools/repl.sh
```
### Reset
```bash
./tools/reset.sh
```

# Device Files

## lib/sensors

### TempSensor Module
The temp module reads the temperature and humidity from a DHT sensor

### Wifi Module
The wifi module scans and connects to a specified network using SSID and PASSWORD

### Led Module
The led module blinks an LED on a specified pin

## /services

### /services/web
The web module makes HTTP requests to a specified URL

### /service/mqtt
The mqtt module subscribes to a specified topic and calls a callback function when a message is received



