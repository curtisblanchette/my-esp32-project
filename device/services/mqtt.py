# /device/services/mqtt.py

from umqtt.simple import MQTTClient
import time

class MqttService:
    def __init__(
            self,
            client_id: str,
            host: str,
            port: int = 1883,
            keepalive: int = 30,
            user: str | None = None,
            password: str | None = None,
    ):
        self.client_id = client_id
        self.host = host
        self.port = port
        self.keepalive = keepalive
        self.user = user
        self.password = password

        self._client = None
        self._connected = False

    def connect(self):
        if self._connected:
            return

        self._client = MQTTClient(
            client_id=self.client_id,
            server=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            keepalive=self.keepalive,
        )

        self._client.connect()
        self._connected = True

    def disconnect(self):
        if self._client:
            try:
                self._client.disconnect()
            except:
                pass
        self._connected = False
        self._client = None

    def publish(self, topic: str, payload: str | bytes, retain=False, qos=0):
        if not self._connected:
            raise RuntimeError("MQTT not connected")

        if isinstance(payload, str):
            payload = payload.encode()

        self._client.publish(
            topic.encode(),
            payload,
            retain=retain,
            qos=qos,
        )

    def ping(self):
        """Force a socket write to detect dead connections"""
        if self._client:
            self._client.ping()

    def is_connected(self) -> bool:
        return self._connected