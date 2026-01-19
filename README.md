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
- connect ESP32 over USB
- ./tools/flash.sh 
- ./tools/repl.sh
- ./tools/reset.sh

## Lib

### wifi
The wifi module will scan and connect to a wifi network

### led
The led module will blink an LED on the ESP32 on pin 5

## Services

### web
The web module will make a GET request to a specified URL



