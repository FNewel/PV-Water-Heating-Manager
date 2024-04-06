"""Number entities for the PV Water Heating Manager integration.

This platform creates two number entities:
- Night Heating Temperature -- Used to set the night pre-heating temperature
- Heating Temperature -- Used to set the boiler heating temperature

Source: https://developers.home-assistant.io/docs/core/entity/number
"""

import logging

from homeassistant.components.number import NumberEntity, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up the number platform.

    Numbers:
    - Night Heating Temperature -- Used to set the night pre-heating temperature
    - Heating Temperature -- Used to set the boiler heating temperature
    """

    nh_temp = NightHeatingTemp(hass, entry)
    heating_temp = HeatingTemp(hass, entry)

    # Store the numbers in the hass data
    hass.data[DOMAIN]["night_heating_temp"] = nh_temp
    hass.data[DOMAIN]["heating_temp"] = heating_temp

    # Add the numbers to the hass instance
    async_add_entities([nh_temp, heating_temp])


class NightHeatingTemp(RestoreNumber, NumberEntity):
    """Representation of a NightHeatingTemp number entity.

    This number entity is used to set the night pre-heating temperature.
    The temperature is set in degrees Celsius.
    It has a range from min_temp to max_temp, which are taken from boiler settings.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the number entity with default values."""
        self._hass = hass
        self._entry = entry
        self._attr_unique_id = "pvwhc_night_heating_temp"
        self.native_value = entry.data["boiler_min_temp"]
        self.native_step = 1.0
        self.native_max_value = entry.data["boiler_max_temp"]
        self.native_min_value = entry.data["boiler_min_temp"]
        self.native_unit_of_measurement = "°C"

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""

        # Get the last state of the number and check if it is in the range
        ret = await self.async_get_last_number_data()
        if ret and not ret.native_value < self.native_min_value or ret.native_value > self.native_max_value:
            self.native_value = ret.native_value
        else:
            self.native_value = self.native_min_value

        self.async_write_ha_state()

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return "Night Heating Temperature"

    @property
    def state(self) -> int | None:
        """Return the state of the entity."""
        return self.native_value

    @callback
    async def _set_value(self, value) -> None:
        """Set the temperature."""
        self.native_value = value
        self.async_write_ha_state()

    async def async_set_native_value(self, value) -> None:
        """Set the temperature."""
        await self._set_value(value)


class HeatingTemp(RestoreNumber, NumberEntity):
    """Representation of a HeatingTemp number entity.

    This number entity is used to set the boiler heating temperature.
    The temperature is set in degrees Celsius.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the number entity with default values."""
        self._hass = hass
        self._entry = entry
        self._attr_unique_id = "pvwhc_heating_temp"
        self.native_value = entry.data["boiler_min_temp"]
        self.native_step = 1.0
        self.native_max_value = entry.data["boiler_max_temp"]
        self.native_min_value = entry.data["boiler_min_temp"]
        self.native_unit_of_measurement = "°C"

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""

        # Get the last states of the number
        ret = await self.async_get_last_number_data()
        if ret:
            self.native_value = ret.native_value

        self.async_write_ha_state()

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return "Heating Temperature"

    @property
    def state(self) -> int | None:
        """Return the state of the entity."""
        return self.native_value

    @callback
    async def _set_value(self, value) -> None:
        """Set the temperature."""
        self.native_value = value

        # Sync the value with the boiler temperature (but only if night heating is off)
        if not self._hass.data[DOMAIN]["night_preheating"]:
            _LOGGER.debug("Setting boiler heating temperature to %s", value)

            # Set temperature
            boiler_thermostat = self._entry.data["boiler_thermostat"]  # ID of the thermostat
            await self._hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": boiler_thermostat, "temperature": value},
                blocking=True,
            )

        self.async_write_ha_state()

    async def async_set_native_value(self, value) -> None:
        """Set the temperature."""
        await self._set_value(value)
