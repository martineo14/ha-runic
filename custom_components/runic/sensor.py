"""Sensors for each Runic vehicle: status, km, engine hours, today's driving, next service…"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    REVOLUTIONS_PER_MINUTE,
    UnitOfTemperature,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfSpeed,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import RunicConfigEntry
from .const import (
    DOMAIN,
    MEASURE_COOLANT_TEMP,
    MEASURE_ENGINE_TEMP_CAN,
    MEASURE_EXTERNAL_VOLTAGE,
    MEASURE_FAULT_CODES,
    MEASURE_FUEL_LITERS,
    MEASURE_FUEL_PERCENT,
    MEASURE_FUEL_PERCENT_OBD,
    MEASURE_MIL_DISTANCE,
    MEASURE_MODULE_VOLTAGE,
    MEASURE_OIL_TEMP,
    MEASURE_RPM,
    MEASURE_RPM_CAN,
    MEASURE_SATELLITES,
    MEASURE_TRACKER_BATTERY,
    MEASURE_TRACKER_BATTERY_ALT,
    MEASURE_TRACKER_BATTERY_V,
    EVENT_RUNIC,
    MEASURE_BATTERY_VOLTAGE,
    MEASURE_ENGINE_HOURS,
    MEASURE_ENGINE_HOURS_CAN,
    MEASURE_FUEL_LITERS_OEM,
    MEASURE_FUEL_PERCENT_CAN,
    MEASURE_FUEL_USED_CAN,
    MEASURE_GSM_SIGNAL,
    MEASURE_HUMIDITY,
    MEASURE_TEMPERATURE,
    MEASURE_VEHICLE_STATUS,
)
from .coordinator import RunicTodayCoordinator, is_moving, poi_at
from .entity import RunicVehicleEntity, async_track_vehicles


def _measure(*ids: int) -> Callable[[dict[str, Any]], Any]:
    def value(vehicle: dict[str, Any]) -> Any:
        measures = vehicle.get("measures") or {}
        return next((measures[i] for i in ids if measures.get(i) is not None), None)

    return value


def _status(vehicle: dict[str, Any]) -> str | None:
    if (vehicle.get("measures") or {}).get(MEASURE_VEHICLE_STATUS) == 2:
        return "offline"
    moving = is_moving(vehicle)
    return None if moving is None else ("driving" if moving else "parked")


def _last_seen(vehicle: dict[str, Any]) -> datetime | None:
    ts = vehicle.get("timestamp")
    return dt_util.utc_from_timestamp(ts) if ts else None


@dataclass(frozen=True, kw_only=True)
class RunicSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]
    # Only created when the vehicle reports the value at setup (not every tracker has a CAN bus).
    exists_fn: Callable[[dict[str, Any]], bool] = lambda vehicle: True


VEHICLE_SENSORS: tuple[RunicSensorDescription, ...] = (
    RunicSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=["driving", "parked", "offline"],
        value_fn=_status,
    ),
    RunicSensorDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_seen,
    ),
    RunicSensorDescription(
        key="speed",
        translation_key="speed",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        suggested_display_precision=0,
        value_fn=lambda v: v.get("speed"),
    ),
    RunicSensorDescription(
        key="odometer",
        translation_key="odometer",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=0,
        value_fn=lambda v: v.get("odometer_km"),
        exists_fn=lambda v: v.get("odometer_km") is not None,
    ),
    RunicSensorDescription(
        key="engine_hours",
        translation_key="engine_hours",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        value_fn=_measure(MEASURE_ENGINE_HOURS_CAN, MEASURE_ENGINE_HOURS),
        exists_fn=lambda v: _measure(MEASURE_ENGINE_HOURS_CAN, MEASURE_ENGINE_HOURS)(v) is not None,
    ),
    RunicSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        value_fn=_measure(MEASURE_BATTERY_VOLTAGE),
        exists_fn=lambda v: _measure(MEASURE_BATTERY_VOLTAGE)(v) is not None,
    ),
    RunicSensorDescription(
        key="fuel_level",
        translation_key="fuel_level",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=_measure(MEASURE_FUEL_PERCENT_CAN, MEASURE_FUEL_PERCENT, MEASURE_FUEL_PERCENT_OBD),
        exists_fn=lambda v: _measure(MEASURE_FUEL_PERCENT_CAN, MEASURE_FUEL_PERCENT, MEASURE_FUEL_PERCENT_OBD)(v) is not None,
    ),
    RunicSensorDescription(
        key="fuel_volume",
        translation_key="fuel_volume",
        device_class=SensorDeviceClass.VOLUME_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        value_fn=_measure(MEASURE_FUEL_LITERS_OEM, MEASURE_FUEL_LITERS),
        exists_fn=lambda v: _measure(MEASURE_FUEL_LITERS_OEM, MEASURE_FUEL_LITERS)(v) is not None,
    ),
    RunicSensorDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=_measure(MEASURE_TEMPERATURE),
        exists_fn=lambda v: _measure(MEASURE_TEMPERATURE)(v) is not None,
    ),
    RunicSensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=_measure(MEASURE_HUMIDITY),
        exists_fn=lambda v: _measure(MEASURE_HUMIDITY)(v) is not None,
    ),
    RunicSensorDescription(
        key="fuel_used",
        translation_key="fuel_used",
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        value_fn=_measure(MEASURE_FUEL_USED_CAN),
        exists_fn=lambda v: _measure(MEASURE_FUEL_USED_CAN)(v) is not None,
    ),
    RunicSensorDescription(
        key="gsm_signal",
        translation_key="gsm_signal",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_measure(MEASURE_GSM_SIGNAL),
        exists_fn=lambda v: _measure(MEASURE_GSM_SIGNAL)(v) is not None,
    ),
)

def _measure_sensor(key: str, ids: tuple[int, ...], **kwargs: Any) -> RunicSensorDescription:
    """A sensor for a status measure, created only when the vehicle reports one of its ids."""
    return RunicSensorDescription(
        key=key,
        translation_key=key,
        value_fn=_measure(*ids),
        exists_fn=lambda v: _measure(*ids)(v) is not None,
        **kwargs,
    )


# Engine health (OBD and CAN trackers) and the tracker's own health.
HEALTH_SENSORS: tuple[RunicSensorDescription, ...] = (
    _measure_sensor("fault_codes", (MEASURE_FAULT_CODES,), state_class=SensorStateClass.MEASUREMENT),
    _measure_sensor(
        "check_engine_distance", (MEASURE_MIL_DISTANCE,),
        device_class=SensorDeviceClass.DISTANCE, state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfLength.KILOMETERS, suggested_display_precision=0,
    ),
    _measure_sensor(
        "coolant_temperature", (MEASURE_COOLANT_TEMP,),
        device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS, suggested_display_precision=0,
    ),
    _measure_sensor(
        "engine_temperature", (MEASURE_ENGINE_TEMP_CAN,),
        device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS, suggested_display_precision=0,
    ),
    _measure_sensor(
        "oil_temperature", (MEASURE_OIL_TEMP,),
        device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS, suggested_display_precision=0,
    ),
    _measure_sensor(
        "rpm", (MEASURE_RPM, MEASURE_RPM_CAN), state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE, suggested_display_precision=0,
    ),
    _measure_sensor(
        "module_voltage", (MEASURE_MODULE_VOLTAGE,),
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT, suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _measure_sensor(
        "tracker_battery", (MEASURE_TRACKER_BATTERY, MEASURE_TRACKER_BATTERY_ALT),
        device_class=SensorDeviceClass.BATTERY, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _measure_sensor(
        "tracker_battery_voltage", (MEASURE_TRACKER_BATTERY_V,),
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT, suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _measure_sensor(
        "external_voltage", (MEASURE_EXTERNAL_VOLTAGE,),
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT, suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _measure_sensor(
        "satellites", (MEASURE_SATELLITES,), state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
    ),
)

TODAY_SENSORS: tuple[RunicSensorDescription, ...] = (
    RunicSensorDescription(
        key="distance_today",
        translation_key="distance_today",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=1,
        value_fn=lambda t: t.get("distance_km"),
    ),
    RunicSensorDescription(
        key="driving_time_today",
        translation_key="driving_time_today",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda t: None if t.get("driving_s") is None else t["driving_s"] / 60,
    ),
    RunicSensorDescription(
        key="trips_today",
        translation_key="trips_today",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda t: t.get("trips"),
    ),
    RunicSensorDescription(
        key="stopped_time_today",
        translation_key="stopped_time_today",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda t: None if t.get("stopped_s") is None else t["stopped_s"] / 60,
    ),
    RunicSensorDescription(
        key="idle_time_today",
        translation_key="idle_time_today",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda t: None if t.get("idle_s") is None else t["idle_s"] / 60,
    ),
    RunicSensorDescription(
        key="max_speed_today",
        translation_key="max_speed_today",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        suggested_display_precision=0,
        value_fn=lambda t: t.get("max_speed"),
    ),
    RunicSensorDescription(
        key="last_trip",
        translation_key="last_trip",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=1,
        value_fn=lambda t: (t.get("last_trip") or {}).get("distance_km"),
    ),
    RunicSensorDescription(
        key="last_trip_end",
        translation_key="last_trip_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda t: dt_util.utc_from_timestamp(t["last_trip"]["end"]) if t.get("last_trip") else None,
    ),
)

# Average consumption only when Runic's daily report has it (CAN fuel data).
AVG_FUEL = RunicSensorDescription(
    key="avg_consumption_today",
    translation_key="avg_consumption_today",
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement="km/L",
    suggested_display_precision=1,
    value_fn=lambda t: t.get("avg_fuel"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: RunicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    today = entry.runtime_data.today
    asked_today: set[int] = set()

    def entities_for(vid: int, vehicle: dict[str, Any]) -> list[SensorEntity]:
        if today.data is not None and vid not in today.data and vid not in asked_today:
            asked_today.add(vid)  # a vehicle added since the last "today" update: fetch its figures now
            hass.async_create_task(today.async_request_refresh())
        return [
            *(
                RunicVehicleSensor(coordinator, vid, d)
                for d in (*VEHICLE_SENSORS, *HEALTH_SENSORS)
                if d.exists_fn(vehicle)
            ),
            *(RunicTodaySensor(today, vid, d) for d in TODAY_SENSORS),
            *(
                [RunicTodaySensor(today, vid, AVG_FUEL)]
                if ((today.data or {}).get(vid) or {}).get("avg_fuel") is not None
                else []
            ),
            RunicKmToService(coordinator, vid),
            RunicNextService(coordinator, vid),
            RunicPoiSensor(coordinator, vid),
        ]

    async_track_vehicles(entry, coordinator, async_add_entities, entities_for)


class RunicVehicleSensor(RunicVehicleEntity, SensorEntity):
    """A value from the vehicle's details or status."""

    entity_description: RunicSensorDescription

    def __init__(self, coordinator, vehicle_id: int, description: RunicSensorDescription) -> None:
        super().__init__(coordinator, vehicle_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.vehicle)


class RunicTodaySensor(CoordinatorEntity[RunicTodayCoordinator], SensorEntity):
    """Today's totals, from Runic's daily report and tracks. They start again from zero each day."""

    _attr_has_entity_name = True
    entity_description: RunicSensorDescription

    def __init__(self, coordinator: RunicTodayCoordinator, vehicle_id: int, description: RunicSensorDescription) -> None:
        super().__init__(coordinator)
        self.vehicle_id = vehicle_id
        self.entity_description = description
        self._attr_unique_id = f"{vehicle_id}_{description.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, str(vehicle_id))})

    @property
    def available(self) -> bool:
        return super().available and self.vehicle_id in (self.coordinator.data or {})

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data[self.vehicle_id])

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "last_trip":
            return None
        trip = self.coordinator.data[self.vehicle_id].get("last_trip")
        if not trip:
            return None
        return {
            "start": dt_util.utc_from_timestamp(trip["start"]).isoformat(),
            "end": dt_util.utc_from_timestamp(trip["end"]).isoformat(),
            "duration_min": round(trip["duration_s"] / 60),
            "max_speed_kmh": trip["max_speed"],
        }


class RunicNextService(RunicVehicleEntity, RestoreSensor):
    """When the next maintenance is due, from Runic's maintenance alert (the API has no deadlines list)."""

    _attr_translation_key = "next_service"
    _attr_device_class = SensorDeviceClass.DATE

    def __init__(self, coordinator, vehicle_id: int) -> None:
        super().__init__(coordinator, vehicle_id, "next_service")
        self._attr_native_value: date | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) and last.native_value:
            self._attr_native_value = (
                last.native_value if isinstance(last.native_value, date) else date.fromisoformat(str(last.native_value))
            )
        if (state := await self.async_get_last_state()) and state.attributes.get("task"):
            self._attr_extra_state_attributes = {"task": state.attributes["task"]}
        self.async_on_remove(self.hass.bus.async_listen(EVENT_RUNIC, self._on_alert))

    @callback
    def _on_alert(self, event: Event) -> None:
        data = event.data
        if data.get("vehicle_id") != self.vehicle_id or data.get("alert") != "maintenance":
            return
        details = data.get("data") or {}
        try:
            days = int(details.get("days"))
        except (TypeError, ValueError):
            return
        self._attr_native_value = dt_util.now().date() + timedelta(days=days)
        self._attr_extra_state_attributes = {"task": details.get("task")}
        self.async_write_ha_state()


class RunicPoiSensor(RunicVehicleEntity, SensorEntity):
    """Which Runic point of interest the vehicle is in, by name, or "away"."""

    _attr_translation_key = "poi"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator, vehicle_id: int) -> None:
        super().__init__(coordinator, vehicle_id, "poi")

    @property
    def options(self) -> list[str]:
        return ["away", *sorted({p["name"] for p in self.coordinator.pois.values()})]

    @property
    def native_value(self) -> str:
        poi = poi_at(self.vehicle, self.coordinator.pois)
        return poi["name"] if poi else "away"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        poi = poi_at(self.vehicle, self.coordinator.pois)
        return {"poi_id": poi["poi_id"] if poi else None}


class RunicKmToService(RunicVehicleEntity, SensorEntity):
    """Km left until the next service: the "Service at" number minus the odometer."""

    _attr_translation_key = "km_to_service"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator, vehicle_id: int) -> None:
        super().__init__(coordinator, vehicle_id, "km_to_service")

    @property
    def native_value(self) -> float | None:
        target = self.coordinator.service_targets.get(self.vehicle_id)
        odometer = self.vehicle.get("odometer_km")
        if target is None or odometer is None:
            return None
        return round(target - odometer)  # negative once the service is overdue
