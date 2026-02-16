"""Unit tests for coordinator.py — cross-device state provider."""

from dataclasses import dataclass

from src.services.coordinator import Coordinator


# ── Mock helpers ──────────────────────────────────────────────────────


@dataclass
class _MockActuator:
    id: str
    type: str = "switch"
    pin: int | None = None
    name: str | None = None
    state: bool | None = False


@dataclass
class _MockCapabilities:
    sensors: list = None
    actuators: list = None

    def __post_init__(self):
        self.sensors = self.sensors or []
        self.actuators = self.actuators or []


@dataclass
class _MockDevice:
    id: str
    location: str
    online: bool = True
    capabilities: _MockCapabilities = None

    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = _MockCapabilities()


class _MockWsServer:
    def __init__(self, latest_by_device: dict | None = None):
        self._data = latest_by_device or {}

    def get_latest_by_device(self, device_id: str):
        return self._data.get(device_id)

    def get_all_latest_by_device(self):
        return dict(self._data)


class _MockSqlite:
    def __init__(self, devices: list[_MockDevice] | None = None):
        self._devices = devices or []

    def get_online_devices(self):
        return [d for d in self._devices if d.online]

    def get_device(self, device_id: str):
        return next((d for d in self._devices if d.id == device_id), None)


# ── Tests ─────────────────────────────────────────────────────────────


class TestCoordinator:
    def test_get_online_device_ids(self):
        sqlite = _MockSqlite([
            _MockDevice(id="a", location="r1", online=True),
            _MockDevice(id="b", location="r2", online=False),
            _MockDevice(id="c", location="r3", online=True),
        ])
        coord = Coordinator(_MockWsServer(), sqlite)
        ids = coord.get_online_device_ids()
        assert set(ids) == {"a", "c"}

    def test_get_latest_reading_exists(self):
        ws = _MockWsServer({"dev1": {"readings": {"temp1": 25.5, "hum1": 60.0}}})
        coord = Coordinator(ws, _MockSqlite())
        assert coord.get_latest_reading("dev1", "temp1") == 25.5
        assert coord.get_latest_reading("dev1", "hum1") == 60.0

    def test_get_latest_reading_missing_device(self):
        coord = Coordinator(_MockWsServer(), _MockSqlite())
        assert coord.get_latest_reading("unknown", "temp1") is None

    def test_get_latest_reading_missing_sensor(self):
        ws = _MockWsServer({"dev1": {"readings": {"temp1": 25.5}}})
        coord = Coordinator(ws, _MockSqlite())
        assert coord.get_latest_reading("dev1", "hum1") is None

    def test_get_all_latest_readings(self):
        ws = _MockWsServer({
            "a": {"readings": {"temp1": 22.0}},
            "b": {"readings": {"temp1": 28.0}},
            "c": {"readings": {"temp1": 19.0}},  # offline
        })
        sqlite = _MockSqlite([
            _MockDevice(id="a", location="r1", online=True),
            _MockDevice(id="b", location="r2", online=True),
            _MockDevice(id="c", location="r3", online=False),
        ])
        coord = Coordinator(ws, sqlite)
        readings = coord.get_all_latest_readings("temp1")
        assert readings == {"a": 22.0, "b": 28.0}
        # "c" excluded because offline

    def test_get_all_latest_readings_filters_offline(self):
        ws = _MockWsServer({"a": {"readings": {"temp1": 22.0}}, "b": {"readings": {"temp1": 28.0}}})
        sqlite = _MockSqlite([
            _MockDevice(id="a", location="r1", online=False),
            _MockDevice(id="b", location="r2", online=False),
        ])
        coord = Coordinator(ws, sqlite)
        assert coord.get_all_latest_readings("temp1") == {}

    def test_device_has_actuator_true(self):
        dev = _MockDevice(
            id="a", location="r1",
            capabilities=_MockCapabilities(actuators=[_MockActuator(id="relay1")]),
        )
        coord = Coordinator(_MockWsServer(), _MockSqlite([dev]))
        assert coord.device_has_actuator("a", "relay1") is True

    def test_device_has_actuator_false(self):
        dev = _MockDevice(
            id="a", location="r1",
            capabilities=_MockCapabilities(actuators=[_MockActuator(id="relay2")]),
        )
        coord = Coordinator(_MockWsServer(), _MockSqlite([dev]))
        assert coord.device_has_actuator("a", "relay1") is False

    def test_device_has_actuator_unknown_device(self):
        coord = Coordinator(_MockWsServer(), _MockSqlite())
        assert coord.device_has_actuator("unknown", "relay1") is False

    def test_get_devices_with_actuator(self):
        devices = [
            _MockDevice(
                id="a", location="r1", online=True,
                capabilities=_MockCapabilities(actuators=[_MockActuator(id="relay1")]),
            ),
            _MockDevice(
                id="b", location="r2", online=True,
                capabilities=_MockCapabilities(actuators=[_MockActuator(id="relay2")]),
            ),
            _MockDevice(
                id="c", location="r3", online=True,
                capabilities=_MockCapabilities(actuators=[_MockActuator(id="relay1")]),
            ),
        ]
        coord = Coordinator(_MockWsServer(), _MockSqlite(devices))
        result = coord.get_devices_with_actuator("relay1")
        assert set(result) == {("a", "r1"), ("c", "r3")}

    def test_get_devices_with_actuator_empty(self):
        coord = Coordinator(_MockWsServer(), _MockSqlite())
        assert coord.get_devices_with_actuator("relay1") == []
