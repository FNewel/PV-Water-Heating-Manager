"""Time platform for PV Water Heating Manager integration.

This platform creates a time entity:
- Morning Time -- Used to set the morning time (Time when the pre-heating should end)

Source: https://developers.home-assistant.io/docs/core/entity/time
"""

from datetime import datetime

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up the time platform.

    Time:
    - Morning Time -- Used to set the morning time (Time when the pre-heating should end)
    """

    morning_time = MorningTime(hass, entry)

    # Store the time in the hass data
    hass.data[DOMAIN]["morning_time_time"] = morning_time

    # Add the time to the hass instance
    async_add_entities([morning_time])


class MorningTime(TimeEntity, RestoreEntity):
    """Representation of a MorningTime time entity."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the time entity with default values."""
        self._hass = hass
        self._entry = entry
        self._attr_unique_id = "pvwhc_morning_time"
        self.native_value = datetime.time(datetime.strptime("00:00", "%H:%M"))

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""

        # Get the last state of the time
        ret = await self.async_get_last_state()
        if ret:
            self.native_value = datetime.time(datetime.strptime(ret.state, "%H:%M:%S"))

        self.async_write_ha_state()

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return "Morning Time"

    @property
    def time(self) -> datetime.time:
        """Return the state of the entity."""
        return self.native_value

    @callback
    async def _set_value(self, value) -> None:
        """Set the time."""
        self.native_value = value
        self.async_write_ha_state()

    async def async_set_value(self, value) -> None:
        """Set the time."""
        await self._set_value(value)
