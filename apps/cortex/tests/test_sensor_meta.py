"""Tests for sensor_meta utility module."""

from src.services.sensor_meta import (
    SENSOR_TYPE_DEFAULTS,
    UNIT_DISPLAY,
    guess_sensor_type,
    sensor_label,
    sensor_rate_unit,
    sensor_unit,
)


class TestSensorUnit:
    def test_device_reported_celsius(self):
        assert sensor_unit("temperature", "celsius") == "°C"

    def test_device_reported_fahrenheit(self):
        assert sensor_unit("temperature", "fahrenheit") == "°F"

    def test_device_reported_percent(self):
        assert sensor_unit("humidity", "percent") == "%"

    def test_device_reported_unknown_passthrough(self):
        """Unknown device unit strings pass through as-is."""
        assert sensor_unit("custom", "furlongs") == "furlongs"

    def test_fallback_temperature(self):
        assert sensor_unit("temperature") == "°C"

    def test_fallback_humidity(self):
        assert sensor_unit("humidity") == "%"

    def test_fallback_soil_moisture(self):
        assert sensor_unit("soil_moisture") == "%"

    def test_fallback_contact(self):
        assert sensor_unit("contact") == ""

    def test_fallback_light_level(self):
        assert sensor_unit("light_level") == "lux"

    def test_fallback_pressure(self):
        assert sensor_unit("pressure") == "hPa"

    def test_fallback_co2(self):
        assert sensor_unit("co2") == "ppm"

    def test_fallback_event(self):
        assert sensor_unit("event") == ""

    def test_unknown_type(self):
        assert sensor_unit("barometric") == ""

    def test_device_unit_takes_priority(self):
        """Device-reported unit overrides type default."""
        assert sensor_unit("temperature", "fahrenheit") == "°F"


class TestSensorLabel:
    def test_known_types(self):
        assert sensor_label("temperature") == "Temperature"
        assert sensor_label("humidity") == "Humidity"
        assert sensor_label("soil_moisture") == "Soil Moisture"
        assert sensor_label("contact") == "Contact"
        assert sensor_label("co2") == "CO\u2082"

    def test_unknown_type_titlecased(self):
        assert sensor_label("barometric") == "Barometric"

    def test_unknown_underscored_type(self):
        assert sensor_label("wind_speed") == "Wind Speed"


class TestSensorRateUnit:
    def test_temperature(self):
        assert sensor_rate_unit("temperature") == "°C/min"

    def test_humidity(self):
        assert sensor_rate_unit("humidity") == "%/min"

    def test_soil_moisture(self):
        assert sensor_rate_unit("soil_moisture") == "%/min"

    def test_contact_empty(self):
        assert sensor_rate_unit("contact") == ""

    def test_unknown(self):
        assert sensor_rate_unit("unknown") == "/min"


class TestGuessSensorType:
    def test_temp_prefix(self):
        assert guess_sensor_type("temp1") == "temperature"
        assert guess_sensor_type("temp_bedroom") == "temperature"

    def test_hum_prefix(self):
        assert guess_sensor_type("hum1") == "humidity"

    def test_soil_prefix(self):
        assert guess_sensor_type("soil1") == "soil_moisture"
        assert guess_sensor_type("soil_bed_3") == "soil_moisture"

    def test_contact_prefix(self):
        assert guess_sensor_type("contact1") == "contact"

    def test_motion_prefix(self):
        assert guess_sensor_type("motion1") == "motion"

    def test_light_prefix(self):
        assert guess_sensor_type("light1") == "light_level"

    def test_co2_prefix(self):
        assert guess_sensor_type("co2_main") == "co2"

    def test_cam_prefix(self):
        assert guess_sensor_type("cam1") == "event"

    def test_pressure_prefix(self):
        assert guess_sensor_type("pressure1") == "pressure"

    def test_unknown_returns_id(self):
        assert guess_sensor_type("custom_xyz") == "custom_xyz"


class TestUnitDisplay:
    def test_known_mappings(self):
        assert UNIT_DISPLAY["celsius"] == "°C"
        assert UNIT_DISPLAY["fahrenheit"] == "°F"
        assert UNIT_DISPLAY["percent"] == "%"
        assert UNIT_DISPLAY["lux"] == "lux"
        assert UNIT_DISPLAY["hpa"] == "hPa"
        assert UNIT_DISPLAY["ppm"] == "ppm"

    def test_all_types_have_defaults(self):
        """Every type in SENSOR_TYPE_DEFAULTS has unit, label, and rate_unit."""
        for stype, meta in SENSOR_TYPE_DEFAULTS.items():
            assert "unit" in meta, f"Missing unit for {stype}"
            assert "label" in meta, f"Missing label for {stype}"
            assert "rate_unit" in meta, f"Missing rate_unit for {stype}"
