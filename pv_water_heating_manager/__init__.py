"""The PV Water Heating Manager component initializations.

Sources:
        https://github.com/home-assistant/example-custom-config/tree/master/custom_components
        https://developers.home-assistant.io/docs/integration_listen_events
        https://community.home-assistant.io/t/custom-component-how-to-implement-scan-interval/385749/5
        https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
        https://github.com/home-assistant/example-custom-config/blob/master/custom_components/mqtt_basic_async/__init__.py
        https://github.com/victronenergy/dbus-mqtt
"""

import contextlib
from datetime import timedelta
import json
import logging
import re

from homeassistant.components import mqtt
from homeassistant.components.mqtt import (
    async_publish,
    async_subscribe,
    async_subscribe_connection_status,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import DOMAIN, TOPICS
from .manager import PVWaterHeatingManager

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up platform from a config entry, so user can add component from GUI."""

    _LOGGER.debug("Starting PV Water Heating Manager component.")

    # Store some default values in hass.data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    # Necessary variables for the component
    hass.data[DOMAIN]["cancel_venus_keepalive"] = None  # Cancel the keepalive task
    hass.data[DOMAIN]["cancel_grid_lost_handler"] = None  # Cancel the grid lost handler task
    hass.data[DOMAIN]["pv_forecast_today_cancel"] = None  # Cancel the today's forecast update task (midnight updates)
    hass.data[DOMAIN][
        "pv_forecast_tomorrow_cancel"
    ] = None  # Cancel the tomorrow's forecast update task (midnight updates)
    hass.data[DOMAIN]["mqtt_subscriptions"] = []  # Store the MQTT subscriptions (So we can unsubscribe later)
    hass.data[DOMAIN][
        "mqtt_timer_event"
    ] = None  # Cancel the MQTT timer event (If MQTT connection is lost longer than 30 seconds)
    hass.data[DOMAIN]["manager_last_state"] = None  # Store the last state of the manager
    hass.data[DOMAIN]["mqtt_connected"] = mqtt.is_connected(hass)  # Tracking MQTT connection status

    hass.data[DOMAIN]["night_heating_event"] = None  # Cancel the night heating event
    hass.data[DOMAIN]["night_heating_calc_event"] = None  # Cancel the night heating calculation event
    hass.data[DOMAIN]["night_heating_planned"] = False  # Used to check if night heating is planned
    hass.data[DOMAIN]["night_heating_calc_planned"] = False  # Used to check if night heating calculation is planned
    hass.data[DOMAIN]["night_heating_canceled"] = False  # Used when there is not enough excess PV (AUTOMATIC mode)
    hass.data[DOMAIN]["night_preheating"] = False  # Used to check if night pre-heating is active
    hass.data[DOMAIN]["component_loading"] = True  # Used to check if the component is loading
    hass.data[DOMAIN]["boiler_power_on"] = False  # Used to check if the boiler is on

    # Set up the PV Water Heating Manager (Manager updates every x seconds - defined by the user)
    # Defined in select.py, under _set_state method, when manager is turned on
    manager = PVWaterHeatingManager(hass, entry)
    hass.data[DOMAIN]["manager"] = manager

    # Forward the setup to the sensor platform.
    await hass.config_entries.async_forward_entry_setup(entry, "sensor")

    # Set the status of the manager to "Initializing"
    hass.data[DOMAIN]["manager_status_sensor"].set_state("Initializing")

    # Forward the setup to the platforms.
    await hass.config_entries.async_forward_entry_setups(entry, ["switch", "time", "number", "select"])

    # When Automatic solar configuration mode was selected, keepalive message is published to Venus MQTT broker and
    # the MQTT listeners are set up.
    if entry.data["solar_conf_mode"] == "automatic":
        mqtt_handler = MQTTMessageHandler(hass, entry)
        hass.data[DOMAIN]["mqtt_handler"] = mqtt_handler

        if hass.data[DOMAIN]["mqtt_connected"]:
            # Publish first keepalive message, so the Venus MQTT broker starts publishing the requested topics
            await async_publish_venus_keepalive(hass, entry)

            # Subscribe to the topics and publish the discovery config for sensors
            await async_setup_mqtt_listeners_and_sensors(hass, entry, mqtt_handler)

            # Set up a recurring task to publish a keepalive message to Venus MQTT broker.
            # Source: https://community.home-assistant.io/t/custom-component-how-to-implement-scan-interval/385749/5
            hass.data[DOMAIN]["cancel_venus_keepalive"] = async_track_time_interval(
                hass, lambda now: hass.create_task(async_publish_venus_keepalive(hass, entry)), timedelta(seconds=30)
            )
        else:
            hass.data[DOMAIN]["manager_status_sensor"].set_state("Off - Warning (MQTT connection lost)")

        async_subscribe_connection_status(hass, mqtt_handler.async_mqtt_connection_changed)

    _LOGGER.debug("PV Water Heating Manager component started.")

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload all entities for this config entry."""

    _LOGGER.debug("Unloading PV Water Heating Manager component.")

    # Turn off manager
    # Manager cancels grid lost handler, night heating, night heating calculation and stop manager updates
    await hass.data[DOMAIN]["manager_status_select"].async_select_option("Off")

    # Cancel the today's forecast update task
    with contextlib.suppress(KeyError), contextlib.suppress(TypeError):
        hass.data[DOMAIN]["pv_forecast_today_cancel"]()
        hass.data[DOMAIN].pop("pv_forecast_today_cancel")

    # Cancel the tomorrow's forecast update task
    with contextlib.suppress(KeyError), contextlib.suppress(TypeError):
        hass.data[DOMAIN]["pv_forecast_tomorrow_cancel"]()
        hass.data[DOMAIN].pop("pv_forecast_tomorrow_cancel")

    if entry.data["solar_conf_mode"] == "automatic":
        # Stop the recurring task to publish a keepalive message to Venus MQTT broker.
        with contextlib.suppress(KeyError), contextlib.suppress(TypeError):
            hass.data[DOMAIN]["cancel_venus_keepalive"]()
            hass.data[DOMAIN].pop("cancel_venus_keepalive")

        # Unsubscribe from all the topics
        for unsub in hass.data[DOMAIN]["mqtt_subscriptions"]:
            with contextlib.suppress(KeyError), contextlib.suppress(TypeError):
                unsub()
        hass.data[DOMAIN].pop("mqtt_subscriptions")

    # Unload entities
    await hass.config_entries.async_forward_entry_unload(entry, "select")
    await hass.config_entries.async_forward_entry_unload(entry, "switch")
    await hass.config_entries.async_forward_entry_unload(entry, "time")
    await hass.config_entries.async_forward_entry_unload(entry, "number")
    await hass.config_entries.async_forward_entry_unload(entry, "sensor")

    # Remove the data from hass.data
    hass.data[DOMAIN].clear()

    _LOGGER.debug("PV Water Heating Manager component unloaded.")

    return True


async def async_setup_mqtt_listeners_and_sensors(hass: HomeAssistant, entry: ConfigEntry, handler) -> None:
    """Set up MQTT listeners and publish discovery config for sensors.

    So all the sensors are automatically discovered and added to Home Assistant.

    Important: When setting up MQTT you need to enable discovery and set the prefix to "homeassistant"

    Source:
            https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
            https://github.com/home-assistant/example-custom-config/blob/master/custom_components/mqtt_basic_async/__init__.py
    """

    _LOGGER.debug("Setting up MQTT listeners and publishing discovery config for sensors.")

    updated_entry_data = {**entry.data}

    # Set phase to None, because it is not known yet
    if "phase" not in updated_entry_data:
        updated_entry_data["phase"] = None

    # Subscribe to the topics only if there are no subscriptions yet
    subscribe = False
    if not hass.data[DOMAIN]["mqtt_subscriptions"]:
        subscribe = True

    # For som reason autodiscovery is not working after restart, so re-subscribe to the topics
    if not subscribe:
        for unsub in hass.data[DOMAIN]["mqtt_subscriptions"]:
            unsub()
        hass.data[DOMAIN]["mqtt_subscriptions"].clear()
        subscribe = True

    # Subscribe to all the topics and publish the discovery config for each sensor, based on the config data.
    for idx, (topic, sensor_config) in enumerate(TOPICS.items()):
        if subscribe:
            _LOGGER.debug("Subscribing to topic: %s", topic)

            # Add topic path based on sensor type
            if sensor_config["type"] == "system":
                topic = f"N/{entry.data["venus_mqtt_topic"]}/system/+/{topic}"
            elif sensor_config["type"] == "critical_load":
                topic = f"N/{entry.data["venus_mqtt_topic"]}/system/+/{topic}/+/Power"
            elif sensor_config["type"] == "battery_ess":
                topic = f"N/{entry.data["venus_mqtt_topic"]}/settings/+/{topic}"
            elif sensor_config["type"] == "battery_power":
                topic = f"N/{entry.data["venus_mqtt_topic"]}/battery/+/Dc/+/{topic}"
            elif sensor_config["type"] == "battery_soc":
                topic = f"N/{entry.data["venus_mqtt_topic"]}/battery/+/{topic}"
            elif sensor_config["type"] == "grid_lost":
                topic = f"N/{entry.data["venus_mqtt_topic"]}/vebus/+/{topic}"
            else:
                _LOGGER.error("Unknown sensor type: %s", sensor_config["type"])
                continue

            # Subscribe to the topic and add the subscription to the hass.data
            sub = await async_subscribe(hass, topic, handler.async_mqtt_message_received)
            hass.data[DOMAIN]["mqtt_subscriptions"].append(sub)

        # Add necessary information to the sensor config
        sensor_id = f"pvwhc_{idx}"
        discovery_topic = f"homeassistant/sensor/{DOMAIN}/{sensor_id}/config"

        if sensor_config["state_class"] == "measurement":
            sensor_config = {
                "name": sensor_config["name"],
                "state_class": sensor_config["state_class"],
                "state_topic": topic,
                "unit_of_measurement": sensor_config["unit_of_measurement"],
                "value_template": sensor_config["value_template"],
                "unique_id": sensor_id,
                "device": {
                    "name": "Venus",
                    "identifiers": ["venus0e"],
                },
            }
        else:
            sensor_config = {
                "name": sensor_config["name"],
                "state_topic": topic,
                "unit_of_measurement": sensor_config["unit_of_measurement"],
                "value_template": sensor_config["value_template"],
                "unique_id": sensor_id,
                "device": {
                    "name": "Venus",
                    "identifiers": ["venus0e"],
                },
            }

        # Add sensor id to the config
        updated_entry_data[sensor_config["name"].replace(" ", "_").lower()] = (
            "sensor.venus_" + sensor_config["name"].replace(" ", "_").lower()
        )

        # Publish the discovery message for the sensor
        await async_publish(hass, discovery_topic, json.dumps(sensor_config))

    # Update the entry data with automatically discovered sensors
    hass.config_entries.async_update_entry(entry, data=updated_entry_data)

    _LOGGER.debug("MQTT listeners set up and discovery config published for sensors.")


async def async_publish_venus_keepalive(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Publish a keepalive message to Venus MQTT broker.

    This message is used to keep the requested topics published by Venus MQTT broker.

    Source: https://github.com/victronenergy/dbus-mqtt
    """

    # Check if MQTT is connected
    if not hass.data[DOMAIN]["mqtt_connected"]:
        return

    topic = f"R/{entry.data["venus_mqtt_topic"]}/keepalive"
    payload = json.dumps(
        [
            "system/+/Ac/Grid/+/Power",
            "system/+/Ac/ConsumptionOnInput/+/Power",
            "system/+/Ac/ConsumptionOnOutput/+/Power",
            "system/+/Dc/Pv/Power",
            "system/+/SystemState/State",
            "battery/+/Dc/+/Power",
            "battery/+/Soc",
            "settings/+/Settings/CGwacs/BatteryLife/State",
            "vebus/+/Alarms/GridLost",
        ]
    )

    _LOGGER.debug("Publishing Venus keepalive message.")
    await async_publish(hass, topic, payload)


class MQTTMessageHandler:
    """Handler for MQTT messages."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the MQTT message handler."""
        self._hass = hass
        self._entry = entry

    @callback
    async def async_mqtt_message_received(self, message) -> None:
        """Handle incoming MQTT messages."""

        # If home phase is not known yet, set it based on the received message
        # topic 'N/xxxx/system/x/Ac/ConsumptionOnOutput/<Phase>/Power'
        with contextlib.suppress(KeyError):
            if not self._entry.data["phase"]:
                # Check if the message payload is not null
                if not json.loads(message.payload)["value"]:
                    return

                match = re.search(r"system/\d+/Ac/ConsumptionOnOutput/L(\d{1})/Power", message.topic)
                if match:
                    updated_entry_data = {**self._entry.data}
                    updated_entry_data["phase"] = match.group(1)
                    self._hass.config_entries.async_update_entry(self._entry, data=updated_entry_data)

                    _LOGGER.debug("Phase set to: %s", updated_entry_data["phase"])

            # For som reason autodiscovery is not working after restart, so call it after mqtt is fully loaded
            if self._hass.data[DOMAIN]["component_loading"]:
                self._hass.data[DOMAIN]["component_loading"] = False
                await async_setup_mqtt_listeners_and_sensors(
                    self._hass, self._entry, self._hass.data[DOMAIN]["mqtt_handler"]
                )

    @callback
    async def async_mqtt_lost_connection(self, now) -> None:
        """Handle lost MQTT connection which lasts 30 seconds."""

        await self._hass.data[DOMAIN]["manager_status_select"].async_select_option("Off")
        self._hass.data[DOMAIN]["manager_status_sensor"].set_state("Off - Warning (MQTT connection lost)")

        _LOGGER.warning("Connection to MQTT broker lost for 30 seconds - Manager set to Off.")

    @callback
    async def async_mqtt_connection_changed(self, event):
        """Handle the MQTT connection status change.

        Event is True if the connection is established, False otherwise.
        """

        _LOGGER.debug("MQTT connection status changed: %s", event)

        # Store last known state of mqtt connection
        last_mqtt_connected = self._hass.data[DOMAIN]["mqtt_connected"]

        # Store the connection status in hass.data
        self._hass.data[DOMAIN]["mqtt_connected"] = event

        # If last state was Off(False) and the connection is established(True), cancel the timer event
        if not last_mqtt_connected and event:
            _LOGGER.debug("MQTT connection established - MQTT timer canceled.")

            # Cancel mqtt timer event
            with contextlib.suppress(KeyError), contextlib.suppress(TypeError):
                self._hass.data[DOMAIN]["mqtt_timer_event"]()

            # Set manager status to last known state
            if self._hass.data[DOMAIN]["manager_last_state"]:
                await self._hass.data[DOMAIN]["manager_status_select"].async_select_option(
                    self._hass.data[DOMAIN]["manager_last_state"]
                )

                if self._hass.data[DOMAIN]["manager_last_state"] == "Off":
                    self._hass.data[DOMAIN]["manager_status_sensor"].set_state("Off")
                else:
                    self._hass.data[DOMAIN]["manager_status_sensor"].set_state("Running")

            # Subscribe to the topics and publish the discovery config for sensors (if not already done)
            if not self._hass.data[DOMAIN]["mqtt_subscriptions"]:
                # Publish first keepalive message, so the Venus MQTT broker starts publishing the requested topics
                await async_publish_venus_keepalive(self._hass, self._entry)

                await async_setup_mqtt_listeners_and_sensors(
                    self._hass, self._entry, self._hass.data[DOMAIN]["mqtt_handler"]
                )

                # Set up a recurring task to publish a keepalive message to Venus MQTT broker.
                # Source: https://community.home-assistant.io/t/custom-component-how-to-implement-scan-interval/385749/5
                self._hass.data[DOMAIN]["cancel_venus_keepalive"] = async_track_time_interval(
                    self._hass,
                    lambda now: self._hass.create_task(async_publish_venus_keepalive(self._hass, self._entry)),
                    timedelta(seconds=30),
                )

        # If last state was On(True) and the connection is lost(False), set the manager to warning and set timer event
        elif last_mqtt_connected and not event:
            # Set the status of the manager to running with mqtt warning
            self._hass.data[DOMAIN]["manager_status_sensor"].set_state("Running - Warning (MQTT connection lost)")
            self._hass.data[DOMAIN]["manager_last_state"] = self._hass.data[DOMAIN]["manager_status_select"].state
            self._hass.data[DOMAIN]["mqtt_timer_event"] = async_call_later(
                self._hass, 30, self.async_mqtt_lost_connection
            )
