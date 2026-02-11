from machine import Pin
import time

class PULSE:
    def __init__(self, pin, duration_ms=500, debounce_ms=1500):
        self.pin = Pin(pin, Pin.OUT)
        self._duration_ms = duration_ms
        self._debounce_ms = debounce_ms
        self._pulse_start = None

    def pulse(self, duration_ms=None):
        self.pin.on()
        self._pulse_start = time.ticks_ms()
        if duration_ms is not None:
            self._duration_ms = duration_ms

    def off(self):
        self.pin.off()

    def tick(self):
        """Call each main loop iteration. Returns True when pulse just completed."""
        if self._pulse_start is None:
            return False
        elapsed = time.ticks_diff(time.ticks_ms(), self._pulse_start)
        if elapsed >= self._duration_ms and self.pin.value():
            self.pin.off()
            return True
        if elapsed >= self._duration_ms + self._debounce_ms:
            self._pulse_start = None
        return False

    @property
    def is_busy(self):
        if self._pulse_start is None:
            return False
        elapsed = time.ticks_diff(time.ticks_ms(), self._pulse_start)
        return elapsed < self._duration_ms + self._debounce_ms

    def value(self):
        return self.pin.value()
