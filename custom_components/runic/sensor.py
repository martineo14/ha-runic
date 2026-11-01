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
from .entity import RunicVehicleEntity


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
        value_fn=lambda v: v.get("speed"),
    ),
    RunicSensorDescription(
        key="odometer",
        translation_key="odometer",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
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
        value_fn=_measure(MEASURE_FUEL_PERCENT_CAN),
        exists_fn=lambda v: _measure(MEASURE_FUEL_PERCENT_CAN)(v) is not None,
    ),
    RunicSensorDescription(
        key="fuel_volume",
        translation_key="fuel_volume",
        device_class=SensorDeviceClass.VOLUME_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        value_fn=_measure(MEASURE_FUEL_LITERS_OEM),
        exists_fn=lambda v: _measure(MEASURE_FUEL_LITERS_OEM)(v) is not None,
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
)


async def async_setup_entry(
    hass: HomeAssistant, entry: RunicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    today = entry.runtime_data.today
    entities: list[SensorEntity] = []
    for vid, vehicle in coordinator.data.items():
        entities += [
            RunicVehicleSensor(coordinator, vid, d) for d in VEHICLE_SENSORS if d.exists_fn(vehicle)
        ]
        entities += [RunicTodaySensor(today, vid, d) for d in TODAY_SENSORS]
        entities.append(RunicNextService(coordinator, vid))
        entities.append(RunicPoiSensor(coordinator, vid))
    async_add_entities(entities)


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
