"""Config flow for PV Water Heating Manager integration, so that users can configure it via the UI.

Source: https://developers.home-assistant.io/docs/config_entries_config_flow_handler
        https://developers.home-assistant.io/docs/data_entry_flow_index
        https://www.home-assistant.io/docs/blueprint/selectors
"""

import contextlib
import logging
import re

import async_timeout  # noqa: TID251
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import selector

from .const import BOILER_REQ_ENTITIES, DOMAIN

_LOGGER = logging.getLogger(__name__)


class PVWaterHeatingControlConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PV Water Heating Manager."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self.config = {}

    async def async_step_user(self, user_input=None):
        """Handle a flow initiated by the user.

        Initialize the configuration based on the user input.
        User can choose between automatic and manual configuration for the Boiler and Solar system.
        Automatic configuration requires specific devices to be available in the system (Venus OS, MQTT broker, etc.).

        Options:
        - integration_name: str (optional) (default "PV Water Heater Manager")
        - boiler_conf_mode: selector (default "automatic")
        - solar_conf_mode: selector (default "automatic")
        """

        # Check if a configuration already exists and abort if so
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is None:
            data_schema = vol.Schema(
                {
                    vol.Optional("integration_name", default="PV Water Heater Manager"): str,
                    vol.Required("boiler_conf_mode", default="automatic"): selector(
                        {
                            "select": {
                                "options": [
                                    {
                                        "value": "automatic",
                                        "label": "Automatic Boiler Configuration",
                                    },
                                    {
                                        "value": "manual",
                                        "label": "Manual Boiler Configuration",
                                    },
                                ]
                            }
                        }
                    ),
                    vol.Required("solar_conf_mode", default="automatic"): selector(
                        {
                            "select": {
                                "options": [
                                    {
                                        "value": "automatic",
                                        "label": "Automatic Solar Configuration",
                                    },
                                    {
                                        "value": "manual",
                                        "label": "Manual Solar Configuration",
                                    },
                                ]
                            }
                        }
                    ),
                    vol.Optional("debug_1"): selector({"entity": {}}),  # TODO: REMOVE
                    vol.Optional("debug_2"): selector({"entity": {}}),  # TODO: REMOVE
                }
            )
            return self.async_show_form(step_id="user", data_schema=data_schema)

        # Append the user input to the configuration
        self.config.update(user_input)
        return await self._navigate_boiler_config()

    async def _navigate_boiler_config(self):
        """Decision-making function to navigate to the appropriate boiler configuration step."""

        if self.config["boiler_conf_mode"] == "automatic":
            return await self.async_step_boiler_automatic()
        return await self.async_step_boiler_manual()

    async def async_step_boiler_automatic(self, user_input=None):
        """Handle the step for automatic boiler configuration.

        User needs to provide the device that was created using ESPHome.

        Options:
        - boiler_device: selector (required)
        - boiler_power: int (required) -- Power of the boiler (eg. 1000W, 1500W, 2000W, etc.).
        - boiler_volume: int (required) -- Volume of the boiler (eg. 80L, 100L, 120L, etc.).
        """

        errors = {}
        data_schema = vol.Schema(
            {
                vol.Required("boiler_device"): selector(
                    {
                        "device": {"filter": {"integration": "esphome"}},
                    }
                ),
                vol.Required("boiler_power"): int,
                vol.Required("boiler_volume"): int,
            }
        )

        if user_input is None:
            return self.async_show_form(step_id="boiler_automatic", data_schema=data_schema)

        # Validate the user input
        if user_input:
            # Check if the device exists in the device registry
            entity_registry = er.async_get(self.hass)
            device_id = user_input["boiler_device"]
            entities = er.async_entries_for_device(entity_registry, device_id)
            if entities is None:
                _LOGGER.error("Device not found in the device registry")
                errors["base"] = "boiler_device_not_found"

            # Iterate over the entities and check if the required entities are available
            required_entities = BOILER_REQ_ENTITIES
            for entity in entities:
                if entity.original_name in required_entities:
                    with contextlib.suppress(ValueError):
                        required_entities.remove(entity.original_name)

            if required_entities:
                _LOGGER.error("Required entities not found in the device")
                errors["base"] = "boiler_required_entities_not_found"

            # Check if the user has entered the power of the boiler in the correct format (Watts not kW)
            if user_input["boiler_power"] < 1000:
                user_input["boiler_power"] *= 1000

            # Show form with errors if any errors occurred
            if errors:
                return self.async_show_form(step_id="boiler_automatic", data_schema=data_schema, errors=errors)

        # Append the user input to the configuration
        self.config.update(user_input)
        # Navigate to the next step (solar configuration)
        return await self._navigate_solar_config()

    async def async_step_boiler_manual(self, user_input=None):
        """Handle the step for manual boiler configuration.

        User needs to provide the entities that will be used for the boiler control.

        Options:
        - boiler_mode: selector [select] (required) -- Boiler mode is used to set the boiler status (antifreeze, smart, prog, manual, etc.). It is used to control the boiler.
        - boiler_heat: selector [binary_sensor] (required) -- Status of the boiler heating element (on/off).
        - boiler_state: selector [sensor] (required) -- Status of the boiler connection (Connected/Disonnected).
        - boiler_temp1: selector [sensor] (optional) -- Temperature of the boilers lower temperature sensor.
        - boiler_temp2: selector [sensor] (required) -- Temperature of the boilers upper temperature sensor (This temp is shown on the display of the boiler for Dražice boilers).
        - boiler_thermostat: selector [climate] (required) -- Thermostat entity that will be used to control the temperature of the boiler.
        - boiler_min_temp: int (required) -- Minimum temperature that the boiler can be set to.
        - boiler_max_temp: int (required) -- Maximum temperature that the boiler can be set to.
        - boiler_power: int (required) -- Power of the boiler (eg. 1000W, 1500W, 2000W, etc.).
        - boiler_volume: int (required) -- Volume of the boiler (eg. 80L, 100L, 120L, etc.).
        """

        if user_input is None:
            data_schema = vol.Schema(
                {
                    vol.Required("boiler_mode"): selector(
                        {
                            "entity": {
                                "filter": {
                                    "domain": "select",
                                }
                            }
                        }
                    ),
                    vol.Required("boiler_heat"): selector(
                        {
                            "entity": {
                                "filter": {
                                    "domain": "binary_sensor",
                                }
                            }
                        }
                    ),
                    vol.Required("boiler_state"): selector(
                        {
                            "entity": {
                                "filter": {
                                    "domain": "sensor",
                                }
                            }
                        }
                    ),
                    vol.Optional("boiler_temp1"): selector(
                        {
                            "entity": {
                                "filter": {
                                    "domain": "sensor",
                                }
                            }
                        }
                    ),
                    vol.Required("boiler_temp2"): selector(
                        {
                            "entity": {
                                "filter": {
                                    "domain": "sensor",
                                }
                            }
                        }
                    ),
                    vol.Required("boiler_thermostat"): selector(
                        {
                            "entity": {
                                "filter": {
                                    "domain": "climate",
                                }
                            }
                        }
                    ),
                    vol.Required("boiler_min_temp"): int,
                    vol.Required("boiler_max_temp"): int,
                    vol.Required("boiler_power"): int,
                    vol.Required("boiler_volume"): int,
                }
            )
            return self.async_show_form(step_id="boiler_manual", data_schema=data_schema)

        if user_input:
            # Check if the user has entered the power of the boiler in the correct format (Watts not kW)
            if user_input["boiler_power"] < 1000:
                user_input["boiler_power"] *= 1000

        # Append the user input to the configuration
        self.config.update(user_input)
        # Navigate to the next step (solar configuration)
        return await self._navigate_solar_config()

    async def _navigate_solar_config(self):
        """Decision-making function to navigate to the appropriate solar configuration step."""

        if self.config["solar_conf_mode"] == "automatic":
            return await self.async_step_solar_automatic()
        return await self.async_step_solar_manual()

    def _clear_mqtt_topic(self, mqtt_topic) -> str | None:
        """Try to extract the MAC address from the Venus MQTT topic, if is written in the incorrect format.

        The MAC address is 12 characters long.

        Returns:
        - str: MAC address if found, None otherwise.

        """

        pattern = r"(?:^|N/|/?)(\w{12})(?=/?|$)"
        match = re.search(pattern, mqtt_topic)

        if match:
            return match.group(1)

        return None

    def _clear_vrm_installation_id(self, installation_id) -> str | None:
        """Try to extract the VRM installation ID from the user input.

        The installation ID is a number.

        Returns:
        - str: Installation ID if found, None otherwise.

        """

        pattern = r"(\d+)"
        match = re.search(pattern, installation_id)

        if match:
            return match.group(1)

        return None

    async def async_step_solar_automatic(self, user_input=None):
        """Handle the step for automatic solar configuration.

        User needs to provide all the necessary information to connect to the Venus OS and VRM.
        If the user does not have the VRM or does not want to use solar forecast, the installation ID and token can be left empty.
        VRM installation ID and token are required to use VRM API to get solar forecast.

        Options:
        - venus_mqtt_topic: str (required) -- Topic that is used to communicate with the Venus OS (MAC address of the Venus OS device "<IP>/N/<topic>").
        - vrm_installation_id: str (optional) -- Installation ID of the VRM (Number in the VRM Portal URL "installation/<installation_id>").
        - vrm_token: str (optional) -- API Token from VRM Portal.
        """

        errors = {}
        data_schema = vol.Schema(
            {
                vol.Required("venus_mqtt_topic"): str,
                vol.Optional("vrm_installation_id"): str,
                vol.Optional("vrm_token"): str,
            }
        )

        if user_input is None:
            return self.async_show_form(step_id="solar_automatic", data_schema=data_schema)

        # Validate the user input
        if user_input:
            # Clean the MQTT topic
            if len(user_input["venus_mqtt_topic"]) != 12:
                ret = self._clear_mqtt_topic(user_input["venus_mqtt_topic"])
                if ret is None:
                    _LOGGER.error("Invalid venus MQTT topic")
                    errors["venus_mqtt_topic"] = "invalid_venus_mqtt_topic"
                user_input["venus_mqtt_topic"] = ret

            # VRM installation ID and token are required to use VRM API
            if user_input.get("vrm_installation_id") and not user_input.get("vrm_token"):
                _LOGGER.error("VRM token is missing")
                errors["vrm_token"] = "missing_vrm_token"
            if user_input.get("vrm_token") and not user_input.get("vrm_installation_id"):
                _LOGGER.error("VRM installation ID is missing")
                errors["vrm_installation_id"] = "missing_vrm_installation_id"

            # Check if the VRM installation ID is valid (installation ID must be a number)
            if user_input.get("vrm_installation_id") and not user_input["vrm_installation_id"].isdigit():
                # Clean the VRM installation ID
                ret = self._clear_vrm_installation_id(user_input["vrm_installation_id"])
                if ret is None:
                    _LOGGER.error("Invalid VRM installation ID")
                    errors["vrm_installation_id"] = "invalid_vrm_installation_id"
                user_input["vrm_installation_id"] = ret

            # Try to connect to the VRM API
            if user_input.get("vrm_installation_id") and user_input.get("vrm_token"):
                try:
                    async with async_timeout.timeout(10):
                        session = async_get_clientsession(self.hass)
                        resp = await session.get(
                            f"https://vrmapi.victronenergy.com/v2/installations/{user_input["vrm_installation_id"]}/stats",
                            headers={"x-authorization": f"Token {user_input["vrm_token"]}"},
                        )
                        if resp.status != 200:
                            _LOGGER.error("Unable to connect to the VRM API")
                            errors["vrm_installation_id"] = "vrm_api_connection_error"
                except Exception as e:
                    _LOGGER.error("Unable to connect to the VRM API: %s", e)
                    errors["vrm_installation_id"] = "vrm_api_connection_error_2"

            # Show form with errors if any errors occurred
            if errors:
                return self.async_show_form(step_id="solar_automatic", data_schema=data_schema, errors=errors)

        # Append the user input to the configuration
        self.config.update(user_input)
        # Navigate to the next step (additional settings)
        return await self.async_step_additionals()

    async def async_step_solar_manual(self, user_input=None):
        """Handle the step for manual solar configuration.

        User needs to provide the entities that will be used for the solar system control.
        System is designed to work with the one-phase solar system, so there is only one "critical_load" entity.

        If the user does not have the VRM or does not want to use solar forecast, the installation ID and token can be left empty.
        VRM installation ID and token are required to use VRM API to get solar forecast.

        Options:
        * It is necessary to select 1 sensor from the grid and 1 from the load, depending on which phase is supported by the solar system. The others are optional, just to display the informations.
        - phase: selector [select] (required) -- Phase which is supported by the solar system (1, 2, 3).
        - grid_l1: selector [sensor] (optional) -- Grid phase L1 power sensor.
        - grid_l2: selector [sensor] (optional) -- Grid phase L2 power sensor.
        - grid_l3: selector [sensor] (optional) -- Grid phase L3 power sensor.
        - load_l1: selector [sensor] (optional) -- Load phase L1 power sensor.
        - load_l2: selector [sensor] (optional) -- Load phase L2 power sensor.
        - load_l3: selector [sensor] (optional) -- Load phase L3 power sensor.
        - critical_load: selector [sensor] (required) -- Critical load power sensor (loads that should be powered in case of grid failure).
        - battery_ess: selector [sensor] (optional) -- Battery ESS power sensor (ESS status).
        - battery_power: selector [sensor] (required) -- Battery power sensor (Power of the battery).
        - battery_soc: selector [sensor] (required) -- Battery state of charge sensor.
        - pv_power: selector [sensor] (required) -- PV power sensor (Solar panels power).
        - system_state: selector [sensor] (optional) -- System state sensor (Discharging, Bulk Charging, etc.).
        - grid_state: selector [sensor] (required) -- Grid state sensor (Grid available or Grid lost).
        - vrm_installation_id: str (optional) -- Installation ID of the VRM (Number in the VRM Portal URL "installation/<installation_id>").
        - vrm_token: str (optional) -- API Token from VRM Portal.
        """

        errors = {}
        data_schema = vol.Schema(
            {
                vol.Required("phase", default="1"): vol.In(["1", "2", "3"]),
                vol.Optional("grid_l1"): selector({"entity": {}}),
                vol.Optional("grid_l2"): selector({"entity": {}}),
                vol.Optional("grid_l3"): selector({"entity": {}}),
                vol.Optional("load_l1"): selector({"entity": {}}),
                vol.Optional("load_l2"): selector({"entity": {}}),
                vol.Optional("load_l3"): selector({"entity": {}}),
                vol.Required("critical_load"): selector({"entity": {}}),
                vol.Optional("battery_ess"): selector({"entity": {}}),
                vol.Required("battery_power"): selector({"entity": {}}),
                vol.Required("battery_soc"): selector({"entity": {}}),
                vol.Required("pv_power"): selector({"entity": {}}),
                vol.Optional("system_state"): selector({"entity": {}}),
                vol.Required("grid_state"): selector({"entity": {}}),
                vol.Optional("vrm_installation_id"): str,
                vol.Optional("vrm_token"): str,
            }
        )

        if user_input is None:
            return self.async_show_form(step_id="solar_manual", data_schema=data_schema)

        # Validate the user input
        if user_input:
            # Check if at least one grid and load sensor is selected
            if not user_input.get("grid_l1") and not user_input.get("grid_l2") and not user_input.get("grid_l3"):
                _LOGGER.error("At least one grid sensor must be selected")
                errors["base"] = "missing_grid_sensor"
            if not user_input.get("load_l1") and not user_input.get("load_l2") and not user_input.get("load_l3"):
                _LOGGER.error("At least one load sensor must be selected")
                errors["base"] = "missing_load_sensor"

            # Check if phase matches the selected sensors
            if user_input["phase"] == "1" and not user_input.get("grid_l1") and not user_input.get("load_l1"):
                _LOGGER.error("Phase 1 selected, but no sensors for phase 1 are selected")
                errors["base"] = "missing_phase_sensor"
            if user_input["phase"] == "2" and not user_input.get("grid_l2") and not user_input.get("load_l2"):
                _LOGGER.error("Phase 2 selected, but no sensors for phase 2 are selected")
                errors["base"] = "missing_phase_sensor"
            if user_input["phase"] == "3" and not user_input.get("grid_l3") and not user_input.get("load_l3"):
                _LOGGER.error("Phase 3 selected, but no sensors for phase 3 are selected")
                errors["base"] = "missing_phase_sensor"

            # VRM installation ID and token are required to use VRM API
            if user_input.get("vrm_installation_id") and not user_input.get("vrm_token"):
                _LOGGER.error("VRM token is missing")
                errors["vrm_token"] = "missing_vrm_token"
            if user_input.get("vrm_token") and not user_input.get("vrm_installation_id"):
                _LOGGER.error("VRM installation ID is missing")
                errors["vrm_installation_id"] = "missing_vrm_installation_id"

            # Check if the VRM installation ID is valid (installation ID must be a number)
            if user_input.get("vrm_installation_id") and not user_input["vrm_installation_id"].isdigit():
                # Clean the VRM installation ID
                ret = self._clear_vrm_installation_id(user_input["vrm_installation_id"])
                if ret is None:
                    _LOGGER.error("Invalid VRM installation ID")
                    errors["vrm_installation_id"] = "invalid_vrm_installation_id"
                user_input["vrm_installation_id"] = ret

            # Try to connect to the VRM API
            if user_input.get("vrm_installation_id") and user_input.get("vrm_token"):
                try:
                    async with async_timeout.timeout(10):
                        session = async_get_clientsession(self.hass)
                        resp = await session.get(
                            f"https://vrmapi.victronenergy.com/v2/installations/{user_input["vrm_installation_id"]}/stats",
                            headers={"x-authorization": f"Token {user_input["vrm_token"]}"},
                        )
                        if resp.status != 200:
                            _LOGGER.error("Unable to connect to the VRM API")
                            errors["vrm_installation_id"] = "vrm_api_connection_error"
                except Exception as e:
                    _LOGGER.error("Unable to connect to the VRM API: %s", e)
                    errors["vrm_installation_id"] = "vrm_api_connection_error_2"

            # Show form with errors if any errors occurred
            if errors:
                return self.async_show_form(step_id="solar_manual", data_schema=data_schema, errors=errors)

        # Append the user input to the configuration
        self.config.update(user_input)
        # Navigate to the next step (additional settings)
        return await self.async_step_additionals()

    async def async_step_additionals(self, user_input=None):
        """Handle the step for additional configuration.

        User needs to provide additional settings for the system to work properly.

        Options:
        - battery_capacity: int (required) -- Capacity of the battery in Wh.
        - battery_soc_top: int (required) -- Battery state of charge top threshold (When value is reached, the heating starts).
        - battery_soc_bottom: int (required) -- Battery state of charge bottom threshold (When value is reached, the heating stops).
        - temp_variable: int (required) -- Temperature variable (Temperature by which the required temperature is subtracted to calculate the surplus).
            For example: the desired temperature is 75C and temp_variable is set to 20C, the system calculates if there will be enough surplus to heat to 55C and if there is still enough surplus, it continues to heat to 75C
                         Basically the minimum temperature.
        - grid_threshold: int (required) -- Grid threshold (Power threshold at which to turn off heating, if this value is exceeded on the grid).
        - manager_updates: int (required) -- Manager updates (Interval at which the manager updates the status of the system).
        """

        errors = {}
        data_schema = vol.Schema(
            {
                vol.Required("battery_capacity", default=4800): vol.All(int, vol.Range(min=0)),
                vol.Required("battery_soc_top", default=65): vol.All(int, vol.Range(min=0, max=100)),
                vol.Required("battery_soc_bottom", default=60): vol.All(int, vol.Range(min=0, max=100)),
                vol.Required("temp_variable", default=20): vol.All(int, vol.Range(min=0, max=100)),
                vol.Required("grid_threshold", default=150): vol.All(int, vol.Range(min=10)),
                vol.Required("manager_updates", default=10): vol.All(int, vol.Range(min=5, max=60)),
            }
        )

        if user_input is None:
            return self.async_show_form(step_id="additionals", data_schema=data_schema)

        # Validate the user input
        if user_input:
            # Check if the user has entered the power of the battery in the correct format (Wh not kWh)
            if user_input["battery_capacity"] < 1000:
                user_input["battery_capacity"] *= 1000

            # Battery SOC bottom must be lower than the top
            if user_input["battery_soc_bottom"] >= user_input["battery_soc_top"]:
                _LOGGER.error("Battery SOC bottom must be lower than the top")
                errors["battery_soc_bottom"] = "battery_soc_bottom"

            # Show form with errors if any errors occurred
            if errors:
                return self.async_show_form(step_id="additionals", data_schema=data_schema, errors=errors)

        # Append the user input to the configuration
        self.config.update(user_input)
        # Navigate to the last step
        return await self.async_step_finish()

    async def async_step_finish(self, user_input=None):
        """Finish the config flow.

        If user has selected automatic configuration, the remaining entities need to be identified.
        Other entities will be added using MQTT Discovery, later.
        """

        if self.config["boiler_conf_mode"] == "automatic":
            # Get boiler's entities
            entity_registry = er.async_get(self.hass)
            device_id = self.config["boiler_device"]
            entities = er.async_entries_for_device(entity_registry, device_id)

            # Store the entities in the configuration
            for entity in entities:
                self.config[entity.original_name.replace(" ", "_").lower()] = entity.entity_id

                if entity.original_name == "Water heater mode":
                    self.config["boiler_mode"] = entity.entity_id
                elif entity.original_name == "Water heater heat":
                    self.config["boiler_heat"] = entity.entity_id
                elif entity.original_name == "Water heater state":
                    self.config["boiler_state"] = entity.entity_id
                elif entity.original_name == "Water heater temp1":
                    self.config["boiler_temp1"] = entity.entity_id
                elif entity.original_name == "Water heater temp2":
                    self.config["boiler_temp2"] = entity.entity_id
                elif entity.original_name == "Water heater thermostat":
                    self.config["boiler_min_temp"] = entity.capabilities.get("min_temp")
                    self.config["boiler_max_temp"] = entity.capabilities.get("max_temp")
                    self.config["boiler_thermostat"] = entity.entity_id

        _LOGGER.debug("Configuration done")

        return self.async_create_entry(title=self.config["integration_name"], data=self.config)
