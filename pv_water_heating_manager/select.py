"""Select platform for the PV Water Heating Manager integration.

This platform creates a select entity:
- Manager Status Select -- Used to change the status of the manager

Source: https://developers.home-assistant.io/docs/core/entity/select
"""
import contextlib
from datetime import timedelta

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up the select platform.

    Select:
    - Manager Status Select -- Used to change the status of the manager
    """

    manager_status_select = ManagerStatusSelect(hass, entry)

    # Store the select in the hass data
    hass.data[DOMAIN]["manager_status_select"] = manager_status_select

    # Add the select to the hass instance
    async_add_entities([manager_status_select])


class ManagerStatusSelect(SelectEntity, RestoreEntity):
    """Representation of a ManagerStatusSelect select entity.

    The select is used to change the status of the manager.

    Options:
    - Automatic -- Manager in automatic mode, heating is controlled by the manager
    - Manual -- Manager in manual mode, heating is controlled by the user (pre-heating)
    - Off -- Manager is off (The entire component is paused)
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the select entity with default values."""
        self._hass = hass
        self._entry = entry
        self._attr_unique_id = "pvwhc_manager_status_select"
        self._state = "Off"

        # If user enters VRM API key and VRM ID, add "Automatic" option
        if entry.data.get("vrm_token") and entry.data.get("vrm_installation_id"):
            self._options = ["Automatic", "Manual", "Off"]
        else:
            self._options = ["Manual", "Off"]

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""

        # Get the last state of the select
        ret = await self.async_get_last_state()
        if ret:
            await self._set_state(ret.state)

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return "Manager Control"

    @property
    def state(self) -> str | None:
        """Return the state of the entity."""
        return self._state

    @property
    def options(self) -> list[str] | None:
        """Return the list of available options."""
        return self._options

    @callback
    async def _set_state(self, state) -> None:
        """Set the state and resume/pause manager updates."""

        manager_status = self.hass.data[DOMAIN]["manager_status_sensor"].state

        # If state is "Off", pause manager updates
        if state == "Off":
            if manager_status != "Initializing":
                with contextlib.suppress(KeyError), contextlib.suppress(TypeError):
                    self.hass.data[DOMAIN]["cancel_manager"]()  # Stop manager updates
                with contextlib.suppress(KeyError), contextlib.suppress(TypeError):
                    self.hass.data[DOMAIN]["cancel_grid_lost_handler"]()  # Stop grid lost handler
                with contextlib.suppress(KeyError), contextlib.suppress(TypeError):
                    self.hass.data[DOMAIN]["night_heating_event"]()  # Cancel night heating
                with contextlib.suppress(KeyError), contextlib.suppress(TypeError):
                    self.hass.data[DOMAIN]["night_heating_calc_event"]()  # Cancel night heating calculation

                # Turn off boiler if it is on (in "MANUAL" mode)
                boiler_state = self._hass.states.get(self._entry.data["boiler_mode"]).state

                if boiler_state == "MANUAL":
                    await self.hass.data[DOMAIN]["manager"]._boiler_power(False)

            # Reset variables
            self.hass.data[DOMAIN]["night_heating_planned"] = False
            self.hass.data[DOMAIN]["night_heating_calc_planned"] = False
            self.hass.data[DOMAIN]["night_heating_canceled"] = False
            self.hass.data[DOMAIN]["night_preheating"] = False
            self.hass.data[DOMAIN]["manager_status_sensor"].set_state("Off")

        # If state changes to "Automatic" or "Manual", resume manager updates
        if state in ["Automatic", "Manual"] and self._state == "Off":
            # Check if MQTT is connected, only if solar configuration mode is automatic
            if self._entry.data["solar_conf_mode"] == "automatic" and not self.hass.data[DOMAIN]["mqtt_connected"]:
                self._hass.data[DOMAIN]["manager_status_sensor"].set_state("Off - Warning (MQTT connection lost)")
                self._state = "Off"
                self.async_write_ha_state()
                return

            # Check if grid is lost, only if not initializing
            if manager_status != "Initializing":
                grid_lost = self.hass.states.get("sensor.venus_grid_lost").state
                if grid_lost != "0":
                    self._hass.data[DOMAIN]["manager_status_sensor"].set_state("Off - Warning (Grid Lost)")
                    self._state = "Off"
                    self.async_write_ha_state()
                    return

            manager = self.hass.data[DOMAIN]["manager"]
            # Set manager reccuring task to run every x seconds (default 10)
            self.hass.data[DOMAIN]["cancel_manager"] = async_track_time_interval(
                self.hass,
                lambda now: self.hass.create_task(manager.run()),
                timedelta(seconds=self.hass.data[DOMAIN].get("manager_updates", 10)),
            )
            # Set grid lost handler
            self.hass.data[DOMAIN]["cancel_grid_lost_handler"] = async_track_state_change_event(
                self.hass, ["sensor.venus_grid_lost"], manager.grid_lost_handler
            )
            self.hass.data[DOMAIN]["manager_status_sensor"].set_state("Running")

        # Set the state
        self._state = state

        self.async_write_ha_state()

    async def async_select_option(self, option) -> None:
        """Change the selected option."""
        await self._set_state(option)
