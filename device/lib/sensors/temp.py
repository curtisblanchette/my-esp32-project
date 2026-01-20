import dht
import machine
import time

class TempSensor:
    def __init__(self, pin=2, sensor="DHT11", retries=3, retry_delay_ms=2000):
        self._pin = machine.Pin(pin)
        self._retries = retries
        self._retry_delay_ms = retry_delay_ms

        sensor_upper = sensor.upper()
        if sensor_upper == "DHT11":
            self._dht = dht.DHT11(self._pin)
        elif sensor_upper == "DHT22":
            self._dht = dht.DHT22(self._pin)
        else:
            raise ValueError("sensor must be 'DHT11' or 'DHT22'")

    def read(self):
        last_err = None
        for _ in range(self._retries):
            try:
                self._dht.measure()
                temp_c = self._dht.temperature()
                humidity = self._dht.humidity()
                print("Temperature: ", temp_c)
                print("Humidity: ", humidity)
                return temp_c, humidity
            except OSError as e:
                last_err = e
                time.sleep_ms(self._retry_delay_ms)

        raise last_err