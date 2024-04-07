"""Sensor platform for the PV Water Heating Manager integration.

This platform creates three sensors:
- Manager Status -- Shows the current status of the manager
- PV generation forecast today -- Shows the forecasted PV generation for today
- PV generation forecast tomorrow -- Shows the forecasted PV generation for tomorrow

Source: https://developers.home-assistant.io/docs/core/entity/sensor
"""

from datetime import datetime, timedelta
import logging
from zoneinfo import ZoneInfo

import async_timeout  # noqa: TID251

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN

SCAN_INTERVAL = timedelta(hours=1)  # How often to fetch forecast data

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    """Set up the sensor platform.

    Sensors:
    - Manager status -- Shows the current status of the manager
    - PV generation forecast today -- Shows the forecasted PV generation for today
    - PV generation forecast tomorrow -- Shows the forecasted PV generation for tomorrow
    """

    entities = []

    manager_status_sensor = ManagerStatusSensor(hass, entry)
    entities.append(manager_status_sensor)
    hass.data[DOMAIN]["manager_status_sensor"] = manager_status_sensor

    # Add the forecast sensors only if VRM token and installation ID are provided
    if entry.data.get("vrm_token") and entry.data.get("vrm_installation_id"):
        pv_generation_forecast_today_sensor = PvGenerationForecastTodaySensor(hass, entry)
        pv_generation_forecast_tomorrow_sensor = PvGenerationForecastTomorrowSensor(hass, entry)

        entities.append(pv_generation_forecast_today_sensor)
        entities.append(pv_generation_forecast_tomorrow_sensor)

        hass.data[DOMAIN]["pv_generation_forecast_today_sensor"] = pv_generation_forecast_today_sensor
        hass.data[DOMAIN]["pv_generation_forecast_tomorrow_sensor"] = pv_generation_forecast_tomorrow_sensor

        # Plan to update the forecast sensors one minute after midnight
        hass.data[DOMAIN]["pv_forecast_today_cancel"] = async_track_time_change(
            hass, pv_generation_forecast_today_sensor.async_update, hour=0, minute=1, second=0
        )
        hass.data[DOMAIN]["pv_forecast_tomorrow_cancel"] = async_track_time_change(
            hass, pv_generation_forecast_tomorrow_sensor.async_update, hour=0, minute=1, second=0
        )

    # Add the sensors to the hass instance
    async_add_entities(entities)


class ManagerStatusSensor(SensorEntity, RestoreEntity):
    """Representation of a Manager Status sensor entity.

    The sensor shows the current status of the manager.

    States:
    - Initializing -- Manager is initializing
    - Running -- Manager is running
    - Off -- Manager is off
    - Off - Warning (Grid Lost) -- Manager is off due to grid loss
    - Off - Warning (Grid Unknown) -- Manager is off due to unknown grid status
    - Off - Warning (MQTT connection lost) -- Manager is off due to lost MQTT connection
    - Running - Warning (MQTT connection lost) -- Manager is running but with lost MQTT connection (It will stop running if mqtt connection is not restored in 30 seconds)
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the sensor with default values."""
        self._hass = hass
        self._entry = entry
        self._attr_unique_id = "pvwhc_manager_status_sensor"
        self.device_class = SensorDeviceClass.ENUM
        self.native_value = "Off"
        self.options = [
            "Initializing",
            "Running",
            "Off",
            "Off - Warning (Grid Lost)",
            "Off - Warning (Grid Unknown)",
            "Off - Warning (MQTT connection lost)",
            "Running - Warning (MQTT connection lost)",
        ]

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        pass

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return "Manager Status"

    @property
    def state(self) -> str | None:
        """Return the state of the entity."""
        return self.native_value

    def set_state(self, state: str) -> None:
        """Set the state of the sensor."""
        self.native_value = state
        self.async_write_ha_state()


class PvGenerationForecastTomorrowSensor(SensorEntity, RestoreEntity):
    """Representation of a PV Generation Forecast sensor entity.

    The sensor shows tomorrow's expected PV generation in Watt-hours.
    Only if VRM token and installation ID are provided.
    Sensor is updated every hour.

    Source:
            https://vrm-api-docs.victronenergy.com/#/ (Missing forecast type)
            https://flows.nodered.org/node/victron-vrm-api
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the sensor with default values."""
        self._hass = hass
        self._entry = entry
        self._attr_unique_id = "pvwhc_pv_generation_forecast_tomorrow_sensor"
        self.device_class = SensorDeviceClass.POWER
        self.native_value = 0
        self.suggested_display_precision = 0
        self.native_unit_of_measurement = "Wh"

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""

        # Get the last state of the sensor
        ret = await self.async_get_last_state()
        if ret:
            self.native_value = int(ret.state)

        # Update sensor if the value is 0, None or last update was more than 30 minutes ago
        if self.native_value == 0 or ret is None or (datetime.now().timestamp() - ret.last_updated_timestamp) > 1800:
            await self.async_update()

        self.async_write_ha_state()

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return "PV Tomorrow's Generation Forecast"

    @property
    def state(self) -> int | None:
        """Return the state of the entity."""
        return self.native_value

    @property
    def should_poll(self) -> bool:
        """Return the polling requirement of the entity."""
        return True

    def _calculate_time_range(self) -> tuple[float, float]:
        """Calculate the time range for the forecast.

        Function calculates the start and end parameters for the forecast API call.
        Converts the current time to tomorrow's start time (i.e. 00:00) and
        tomorrow's end time (i.e. 23:59) and returns these values as a timestamps.

        Source: https://docs.python.org/3/library/datetime.html

        Returns:
            tuple[float, float]: Start and end times as timestamps

        """

        # Get the current datetime
        cest_tz = ZoneInfo("Europe/Bratislava")
        now = datetime.now(cest_tz)

        # Calculate tomorrow's start and end time
        start_time = now.replace(hour=0, minute=0, second=0) + timedelta(days=1)
        end_time = now.replace(hour=23, minute=59, second=59) + timedelta(days=1)

        # Return the start and end times as timestamps
        return start_time.timestamp(), end_time.timestamp()

    @callback
    async def async_update(self):
        """Update the Forecast sensor.

        Function fetches the forecast data from the VRM API and updates the sensor state.

        Source:
            https://developers.home-assistant.io/docs/integration_fetching_data
        """

        installation_id = self._entry.data.get("vrm_installation_id")
        token = self._entry.data.get("vrm_token")
        start, end = self._calculate_time_range()
        url = f"https://vrmapi.victronenergy.com/v2/installations/{installation_id}/stats?type=forecast&start={start}&end={end}&interval=days"

        _LOGGER.debug("Updating tomorrow's PV Generation Forecast Sensor")

        try:
            with async_timeout.timeout(10):
                session = async_get_clientsession(self.hass)
                response = await session.get(url, headers={"x-authorization": f"Token {token}"})
                data = await response.json()
                self.native_value = int(data["totals"]["solar_yield_forecast"])
                self.async_write_ha_state()
        except Exception as error:
            raise UpdateFailed(error) from error


class PvGenerationForecastTodaySensor(SensorEntity, RestoreEntity):
    """Representation of a PV Generation Forecast sensor entity.

    The sensor shows today's expected PV generation in Watt-hours.
    Only if VRM token and installation ID are provided.
    Sensor is updated every hour.

    Source:
            https://vrm-api-docs.victronenergy.com/#/ (Missing forecast type)
            https://flows.nodered.org/node/victron-vrm-api
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the sensor with default values."""
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = "pvwhc_pv_generation_forecast_today_sensor"
        self.device_class = SensorDeviceClass.POWER
        self.native_value = 0
        self.suggested_display_precision = 0
        self.native_unit_of_measurement = "Wh"

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""

        # Get the last state of the sensor
        ret = await self.async_get_last_state()
        if ret:
            self.native_value = int(ret.state)

        # Update sensor if the value is 0, None or last update was more than 30 minutes ago
        if self.native_value == 0 or ret is None or (datetime.now().timestamp() - ret.last_updated_timestamp) > 1800:
            await self.async_update()

        self.async_write_ha_state()

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return "PV Today's Generation Forecast"

    @property
    def state(self) -> int | None:
        """Return the state of the entity."""
        return self.native_value

    @property
    def should_poll(self) -> bool:
        """Return the polling requirement of the entity."""
        return True

    def _calculate_time_range(self) -> tuple[float, float]:
        """Calculate the time range for the forecast.

        Function calculates the start and end parameters for the forecast API call.
        Converts the current time to today's start time (i.e. 00:00) and
        today's end time (i.e. 23:59) and returns these values as timestamps.

        Source: https://docs.python.org/3/library/datetime.html

        Returns:
            tuple[float, float]: Start and end times as timestamps

        """

        # Get the current datetime
        cest_tz = ZoneInfo("Europe/Bratislava")
        now = datetime.now(cest_tz)

        # Calculate today's start and end time
        start_time = now.replace(hour=0, minute=0, second=0)
        end_time = now.replace(hour=23, minute=59, second=59)

        # Return the start and end times as timestamps
        return start_time.timestamp(), end_time.timestamp()

    @callback
    async def async_update(self, now=None):
        """Update the Forecast sensor.

        Function fetches the forecast data from the VRM API and updates the sensor state.

        Source:
            https://developers.home-assistant.io/docs/integration_fetching_data
        """

        installation_id = self.entry.data.get("vrm_installation_id")
        token = self.entry.data.get("vrm_token")
        start, end = self._calculate_time_range()
        url = f"https://vrmapi.victronenergy.com/v2/installations/{installation_id}/stats?type=forecast&start={start}&end={end}&interval=days"

        _LOGGER.debug("Updating today's PV Generation Forecast Sensor")

        try:
            with async_timeout.timeout(10):
                session = async_get_clientsession(self.hass)
                response = await session.get(url, headers={"x-authorization": f"Token {token}"})
                data = await response.json()
                self.native_value = int(data["totals"]["solar_yield_forecast"])
                self.async_write_ha_state()
        except Exception as error:
            raise UpdateFailed(error) from error
