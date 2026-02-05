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
        self._last_will = None
        self._callback = None
        self._subscriptions = []

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

        # Apply last will if configured (must be before connect)
        if self._last_will:
            self._client.set_last_will(
                self._last_will["topic"],
                self._last_will["msg"],
                retain=self._last_will.get("retain", False),
                qos=self._last_will.get("qos", 0)
            )

        self._client.connect()
        self._connected = True

        # Apply callback after connect (umqtt.simple requires this order)
        if self._callback:
            self._client.set_callback(self._callback)

        # Re-subscribe to any topics after reconnect
        for topic in self._subscriptions:
            self._client.subscribe(topic)

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

    def set_last_will(self, topic: str, msg: str, retain: bool = False, qos: int = 0):
        """
        Configure last-will message. Must be called BEFORE connect().

        Args:
            topic: Topic to publish will message to
            msg: Message payload (string)
            retain: Whether to retain the will message
            qos: QoS level (0 or 1)
        """
        self._last_will = {
            "topic": topic.encode() if isinstance(topic, str) else topic,
            "msg": msg.encode() if isinstance(msg, str) else msg,
            "retain": retain,
            "qos": qos
        }

    def subscribe(self, topic: str, qos: int = 0):
        """
        Subscribe to a topic.

        Args:
            topic: Topic pattern to subscribe to (supports wildcards)
            qos: QoS level
        """
        encoded_topic = topic.encode() if isinstance(topic, str) else topic
        if encoded_topic not in self._subscriptions:
            self._subscriptions.append(encoded_topic)

        if self._connected and self._client:
            self._client.subscribe(encoded_topic, qos)

    def set_callback(self, callback):
        """
        Set callback for incoming messages.

        Args:
            callback: Function(topic: bytes, msg: bytes) called on message receipt
        """
        self._callback = callback
        if self._connected and self._client:
            self._client.set_callback(callback)

    def check_msg(self):
        """
        Non-blocking check for incoming messages.

        Call this regularly in the main loop to process subscribed messages.
        """
        if self._connected and self._client:
            self._client.check_msg()