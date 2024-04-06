"""Switch platform for the PV Water Heating Manager integration.

This platform creates a switch entity:
- Manager Night Heating -- Used to enable/disable night pre-heating

Source: https://developers.home-assistant.io/docs/core/entity/switch
"""
import contextlib

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up the switch platform.

    Switch:
    - Manager Night Heating -- Used to enable/disable night pre-heating
    """

    nh_toggle_switch = NightHeatingSwitch(hass, entry)

    # Store the switch in the hass data
    hass.data[DOMAIN]["night_heating_switch"] = nh_toggle_switch

    # Add the switch to the hass instance
    async_add_entities([nh_toggle_switch])


class NightHeatingSwitch(SwitchEntity, RestoreEntity):
    """Representation of a NightHeatingSwitch switch entity.

    The switch is used to enable/disable night pre-heating.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the switch entity with default values."""
        self._hass = hass
        self._entry = entry
        self._attr_unique_id = "pvwhc_night_heating_switch"
        self._state = False

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""

        # Get the last state of the switch
        ret = await self.async_get_last_state()
        if ret:
            await self._toggle_state(ret.state == "on")

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return "Night pre-heating"

    @property
    def is_on(self) -> bool:
        """Return the state of the switch."""
        return bool(self._state)

    @callback
    async def _toggle_state(self, value) -> None:
        """Toggle the switch state."""
        self._state = value
        manager_status = self.hass.data[DOMAIN]["manager_status_sensor"].state

        # Cancel night pre-heating
        if not value:
            if manager_status != "Initializing":
                with contextlib.suppress(KeyError), contextlib.suppress(TypeError):
                    self.hass.data[DOMAIN]["night_heating_event"]()  # Cancel night heating
                with contextlib.suppress(KeyError), contextlib.suppress(TypeError):
                    self.hass.data[DOMAIN]["night_heating_calc_event"]()  # Cancel night heating calculation

            # Turn off boiler, if it is on by night pre-heating
            if self.hass.data[DOMAIN]["night_preheating"]:
                boiler_heating = self._hass.states.get(self._entry.data["boiler_heat"]).state

                if boiler_heating == "on":
                    self.hass.data[DOMAIN]["manager"]._boiler_power(False)

            # Reset variables
            self.hass.data[DOMAIN]["night_heating_planned"] = False
            self.hass.data[DOMAIN]["night_heating_calc_planned"] = False
            self.hass.data[DOMAIN]["night_heating_canceled"] = False
            self.hass.data[DOMAIN]["night_preheating"] = False

        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the switch on."""
        await self._toggle_state(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the switch off."""
        await self._toggle_state(False)
