"""PV Water Heating Manager.

This module contains the main logic of the PV Water Heating Manager.
The manager decides when to turn the boiler on and off, schedules night pre-heating.

Sources:
        https://sciencing.com/calculate-temperature-btu-6402970.html
        https://community.home-assistant.io/t/custom-component-how-to-implement-scan-interval/385749/5
        https://www.home-assistant.io/integrations/history/
        https://community.home-assistant.io/t/trying-to-isolate-slow-history/279016
"""

from datetime import date, datetime, time, timedelta
import logging

from homeassistant.components.recorder import history
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
import homeassistant.util.dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class PVWaterHeatingManager:
    """Representation of the PV Water Heating Manager."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the PV Water Heating Manager."""
        self._hass = hass
        self._entry = entry

    async def run(self) -> None:
        """Run the main logic of the PV Water Heating Manager."""

        # Check if boiler is connected
        boiler_connection = await self._get_sensor_state(self._entry.data["boiler_state"], "string")
        manager_status = self._hass.data[DOMAIN]["manager_status_sensor"].state
        if boiler_connection == "Disconnected":
            _LOGGER.warning("Boiler is disconnected")
            if manager_status != "Warning (Boiler Disconnected)":
                self._hass.data[DOMAIN]["manager_status_sensor"].set_state("Warning (Boiler Disconnected)")
            return

        # Check if MQTT is connected (Solar through automatic configuration)
        if self._entry.data["solar_conf_mode"] == "automatic" and not self._hass.data[DOMAIN]["mqtt_connected"]:
            _LOGGER.warning("MQTT is not connected")
            return

        # Run the boiler night pre-heating logic
        await self.night_pre_heating()

        # Run the boiler control logic (only if night pre-heating is not heating)
        if not self._hass.data[DOMAIN]["night_preheating"]:
            await self.boiler_logic()

    async def boiler_logic(self) -> None:
        """Control the boiler based on obtained data.

        After successfully obtaining all the necessary data, it will decide whether the boiler can be switched on or off.
        """

        # Get the phase which is suported by solar system (so logic can be applied to the correct phase)
        # If automatic configuration is used, the phase is obtained from the MQTT data
        phase = self._entry.data["phase"]
        if not phase:
            _LOGGER.debug("BL: Phase is not set (waiting for mqtt data)")
            return

        # Get values set in the configuration by the user
        boiler_power = int(self._entry.data["boiler_power"])
        battery_top_threshold = int(self._entry.data["battery_soc_top"])
        battery_bottom_threshold = int(self._entry.data["battery_soc_bottom"])
        grid_threshold = int(self._entry.data["grid_threshold"])

        # Get the current state of the sensors
        grid_power = await self._get_sensor_state(self._entry.data[f"grid_l{phase}"], "float")
        critical_loads = await self._get_sensor_state(self._entry.data["critical_load"], "float")
        critical_loads_history = await self._get_sensor_history(self._entry.data["critical_load"], secs=10)
        pv_power_history = await self._get_sensor_history(
            self._entry.data["pv_power"], mins=10
        )  # History of last 10 minutes
        battery_soc = await self._get_sensor_state(self._entry.data["battery_soc"], "float")
        boiler_heating = self._hass.states.get(self._entry.data["boiler_heat"]).state
        boiler_temp_to_heat = self._hass.data[DOMAIN]["heating_temp"].state

        # Check if boiler is heating
        if boiler_heating == "on":
            # Battery needs to be charged at least to the bottom threshold
            if battery_soc < battery_bottom_threshold:
                _LOGGER.debug(
                    "BL(ON->OFF): Battery has dropped below the set threshold [%s:%s]",
                    battery_soc,
                    battery_bottom_threshold,
                )
                await self._boiler_power(False)
                await self.debug_value(2, "BATTERY DROP BELOW THRESHOLD")  # TODO: REMOVE
                return

            # If for some reason energy is also taken from the grid, it should not exceed the set threshold
            if grid_power > grid_threshold:
                _LOGGER.debug(
                    "BL(ON->OFF): Too much power is taken from the grid (threshold exceeded) [%s:%s]",
                    grid_power,
                    grid_threshold,
                )
                await self._boiler_power(False)
                await self.debug_value(2, "TOO MUCH POWER TAKEN FROM GRID (1)")  # TODO: REMOVE
                return

            # Critical loads should not exceed this threshold
            if critical_loads_history >= (boiler_power + 1 / 3 * boiler_power):
                _LOGGER.debug(
                    "BL(ON->OFF): Critical loads are high enough to not power the boiler (~+500W) [%s:%s]",
                    critical_loads_history,
                    boiler_power + 1 / 3 * boiler_power,
                )
                await self._boiler_power(False)
                await self.debug_value(2, "CRITICAL LOADS ARE HIGH ENOUGH (1)")  # TODO: REMOVE
                return

            # PV should generate enough power to cover the critical loads
            if pv_power_history < critical_loads:
                _LOGGER.debug(
                    "BL(ON..): PV is not generating enough to power the critical loads [%s:%s]",
                    pv_power_history,
                    critical_loads,
                )

                # The battery should cover minor variations but should not exceed 1/3 of the boilers power
                if pv_power_history < (1 / 3 * boiler_power):
                    _LOGGER.debug(
                        "BL(ON->OFF): PV is not generating enough to power the boiler [%s:%s]",
                        pv_power_history,
                        1 / 3 * boiler_power,
                    )
                    await self._boiler_power(False)
                    await self.debug_value(2, "PV IS NOT GENERATING ENOUGH (1)")  # TODO: REMOVE
                    return

        # Boiler is not heating
        else:
            # Battery needs to be charged at least to the top threshold to start heating
            if battery_soc < battery_top_threshold:
                _LOGGER.debug(
                    "BL(OFF): Battery is not charged enough (below the top threshold) [%s:%s]",
                    battery_soc,
                    battery_top_threshold,
                )
                await self.debug_value(2, "BATTERY NOT CHARGER ENOUGH")  # TODO: REMOVE
                return

            # If for some reason energy is also taken from the grid, it should not exceed the set threshold
            if grid_power > grid_threshold:
                _LOGGER.debug(
                    "BL(OFF): Too much power is taken from the grid (threshold exceeded) [%s:%s]",
                    grid_power,
                    grid_threshold,
                )
                await self.debug_value(2, "TOO MUCH POWER TAKEN FROM GRID (2)")  # TODO: REMOVE
                return

            # Critical loads should not exceed this threshold
            if critical_loads_history >= (boiler_power + 1 / 3 / boiler_power):
                _LOGGER.debug(
                    "BL(OFF): Critical loads are high enough to not power the boiler (~+500W) [%s:%s]",
                    critical_loads_history,
                    boiler_power + 1 / 3 / boiler_power,
                )
                await self.debug_value(2, "CRITICAL LOADS ARE HIGH ENOUGH (2)")  # TODO: REMOVE
                return

            # PV should generate at least 1/3 of the boiler's power to start heating
            if pv_power_history < (1 / 3 * boiler_power):
                _LOGGER.debug(
                    "BL(OFF): PV is not generating enough to power the boiler [%s:%s]",
                    pv_power_history,
                    1 / 3 * boiler_power,
                )
                await self.debug_value(2, "PV IS NOT GENERATING ENOUGH (2)")  # TODO: REMOVE
                return

            # Start heating the water
            await self._boiler_power(True, boiler_temp_to_heat)

    async def night_pre_heating(self) -> None:
        """Run the night pre-heating logic.

        It decides when to start scheduling night pre-heating, based on yesterday's minimum boiler temperature before the morning time
        or the current boiler temperature (whichever is lower) and the user's settings.

        Source of datetime calculation: https://stackoverflow.com/a/39651061
        """

        # Don't plan the night pre-heating if it's already planned, canceled, or in progress
        night_heating_planned = self._hass.data[DOMAIN]["night_heating_planned"]
        night_heating_calc_planned = self._hass.data[DOMAIN]["night_heating_calc_planned"]
        night_heating_canceled = self._hass.data[DOMAIN]["night_heating_canceled"]
        night_preheating = self._hass.data[DOMAIN]["night_preheating"]
        if night_heating_planned or night_heating_calc_planned or night_heating_canceled or night_preheating:
            return

        # If night pre-heating is disabled
        night_heating = self._hass.data[DOMAIN]["night_heating_switch"].is_on
        if not night_heating:
            return

        _LOGGER.debug("NPH: Running the night pre-heating logic (after checks)")

        # Plan when pre-heat should calculate the time to start heating
        # If the calculation is started early, the time and energy required will not be accurate
        morning_time = self._hass.data[DOMAIN]["morning_time_time"].time
        yesterday_morning_time = datetime.combine(datetime.now().date() - timedelta(days=1), morning_time)
        yesterday_boiler_temp = await self._get_sensor_history(
            self._entry.data["boiler_temp2"], mins=300, s_time=yesterday_morning_time, min_val=True
        )  # Yesterday's minimum boiler temp from 5 hours before the morning time to the morning time
        boiler_temp_now = await self._get_sensor_state(self._entry.data["boiler_temp2"], "float")
        calc_temp = min(yesterday_boiler_temp, boiler_temp_now)  # Use the lower temperature
        preheat_temp = self._hass.data[DOMAIN]["night_heating_temp"].state
        boiler_power = int(self._entry.data["boiler_power"])
        boiler_volume = int(self._entry.data["boiler_volume"])

        # Calculate the time to heat the water
        boiler_heat = await self._calculate_boiler_heat(boiler_power, boiler_volume, calc_temp, preheat_temp)
        needed_time = (
            boiler_heat[1] + 120
        )  # Add 2 hours to the time to heat the water, so it should be planned early enough

        _LOGGER.debug(
            "NPH: Night pre-heating calculation morning time [%s], yesterday's boiler temp [%s], current boiler temp [%s], preheat temp [%s], needed time [%s]",
            morning_time,
            yesterday_boiler_temp,
            boiler_temp_now,
            preheat_temp,
            needed_time,
        )

        # If boiler heat is 0, the water is already heated to the desired temperature
        # Then plan the night pre-heating 2 hours before the morning time (just in case) - It will be canceled if the water is already heated
        if boiler_heat[0] == 0:
            planned_time = datetime.combine(date.min, morning_time) - timedelta(hours=2)
        else:
            planned_time = datetime.combine(date.min, morning_time) - timedelta(minutes=needed_time)

        # Plan the night pre-heating calculation
        self._hass.data[DOMAIN]["night_heating_calc_event"] = async_track_time_change(
            self._hass,
            self._plan_start_night_pre_heating,
            hour=planned_time.hour,
            minute=planned_time.minute,
            second=0,
        )
        self._hass.data[DOMAIN]["night_heating_calc_planned"] = True

        _LOGGER.debug(
            "NPH: Night pre-heating logic finished, night pre-heating calculation planned [%s]", planned_time
        )

    async def _plan_start_night_pre_heating(self, now) -> None:
        """Logic to plan the night pre-heating.

        This is run some time before the pre-heat should be run to detect the actual water temperature and not be affected by early scheduling.

        Logic is driven by the manager status:
        - Manager in Manual mode: Pre-heating is planned based on the user's settings (Forecast is not taken into account)
        - Manager in Automatic mode: Pre-heating is planned based on the forecast, which takes into account battery charging and water heating throughout the day

        Source of datetime calculation: https://stackoverflow.com/a/39651061
        """

        _LOGGER.debug("PNPH: Planning the night pre-heating")

        # Cancel the night pre-heating calculation
        self._hass.data[DOMAIN]["night_heating_calc_event"]()

        manager_status = self._hass.data[DOMAIN]["manager_status_select"].state
        boiler_water_temp = await self._get_sensor_state(self._entry.data["boiler_temp2"], "float")
        boiler_power = int(self._entry.data["boiler_power"])
        boiler_volume = int(self._entry.data["boiler_volume"])
        preheat_temp = self._hass.data[DOMAIN]["night_heating_temp"].state
        morning_time = self._hass.data[DOMAIN]["morning_time_time"].time

        boiler_heat = await self._calculate_boiler_heat(boiler_power, boiler_volume, boiler_water_temp, preheat_temp)
        start_time = datetime.combine(date.min, morning_time) - timedelta(minutes=boiler_heat[1])

        # If boiler heat is 0, the water is already heated to the desired temperature
        # Block the night pre-heating and plan the end of the night pre-heating
        if boiler_heat[0] == 0:
            self._hass.data[DOMAIN]["night_heating_canceled"] = True
            self._hass.data[DOMAIN]["night_heating_event"] = async_track_time_change(
                self._hass, self._end_pre_heating, hour=morning_time.hour, minute=morning_time.minute, second=0
            )

            _LOGGER.debug("PNPH: Pre-heating is not planned (water is already heated to the desired temperature)")

            # Remove the planned calculation
            self._hass.data[DOMAIN]["night_heating_calc_planned"] = False
            return

        if manager_status == "Manual":
            # Plan the night pre-heating
            self._hass.data[DOMAIN]["night_heating_event"] = async_track_time_change(
                self._hass, self._start_night_pre_heating, hour=start_time.hour, minute=start_time.minute, second=0
            )
            self._hass.data[DOMAIN]["night_heating_planned"] = True

            _LOGGER.debug("PNPH: Pre-heating is planned (manager in manual mode) [%s/%s]", start_time, preheat_temp)

        # Manager is in automatic mode, so pre-heating is controlled by the manager (based on the forecast)
        else:
            heating_temp = self._hass.data[DOMAIN]["heating_temp"].state  # Heating temperature (Day) set by the user
            temp_variation = self._entry.data["temp_variable"]  # Temperature variation set by the user
            min_boiler_temp = self._entry.data["boiler_min_temp"]  # Minimum boiler temperature
            battery_soc = await self._get_sensor_state(self._entry.data["battery_soc"], "int")
            battery_capacity = int(self._entry.data["battery_capacity"])  # Battery capacity in Wh
            battery_threshold_top = self._entry.data["battery_soc_top"]  # Battery top threshold
            desired_batt_cap = battery_capacity * battery_threshold_top / 100  # Desired battery capacity in Wh

            time_now = datetime.now().time()
            midnight = time(0, 0)

            # Get forecasted PV generation, today's forecast if it's after midnight
            if time_now > midnight:
                pv_forecast = self._hass.data[DOMAIN]["pv_generation_forecast_today_sensor"].state
            else:
                pv_forecast = self._hass.data[DOMAIN]["pv_generation_forecast_tomorrow_sensor"].state

            # Calculate minimum temperature to heat the water to (heating temperature - variation can be lower than the minimum boiler temperature)
            # Calculate the difference between the minimum temperature and the pre-heat temperature
            min_temp = max(heating_temp - temp_variation, min_boiler_temp)
            delta_temp = max(min_temp - preheat_temp, 0)

            # Calculate the energy needed to heat the water from the pre-heat temperature to the minimum temperature throughout the day
            # Calculate the energy needed to charge the battery to the top threshold
            boiler_energy_day = await self._calculate_boiler_heat(
                boiler_power, boiler_volume, preheat_temp, delta_temp
            )
            battery_energy = await self._calculate_battery_energy(desired_batt_cap, battery_soc)

            # If calculated energy is enough to heat the water and charge the battery, plan the night pre-heating
            if boiler_energy_day[0] + battery_energy <= pv_forecast / 1000:
                self._hass.data[DOMAIN]["night_heating_event"] = async_track_time_change(
                    self._hass, self._start_night_pre_heating, hour=start_time.hour, minute=start_time.minute, second=0
                )
                self._hass.data[DOMAIN]["night_heating_planned"] = True

                _LOGGER.debug(
                    "PNPH: Pre-heating is planned (manager in automatic mode) [%s/%s]", start_time, preheat_temp
                )
            else:
                self._hass.data[DOMAIN]["night_heating_canceled"] = True
                self._hass.data[DOMAIN]["night_heating_event"] = async_track_time_change(
                    self._hass, self._end_pre_heating, hour=morning_time.hour, minute=morning_time.minute, second=0
                )

                _LOGGER.debug("PNPH: Pre-heating is not planned (not enough energy)")

        # Remove the planned calculation
        self._hass.data[DOMAIN]["night_heating_calc_planned"] = False

        _LOGGER.debug("PNPH: Planning the night pre-heating finished")

    async def _start_night_pre_heating(self, now) -> None:
        """Start the night pre-heating.

        It will start heating the water in the boiler to the desired temperature.
        If the water is already heated to the desired temperature, it will not start heating.
        Plan the end of the night pre-heating.
        """

        _LOGGER.debug("SNPH: Starting the night pre-heating")

        # Cancel the night pre-heating
        self._hass.data[DOMAIN]["night_heating_event"]()

        # Check if water is already heated to the desired temperature
        boiler_water_temp = await self._get_sensor_state(self._entry.data["boiler_temp2"], "float")
        preheat_temp = self._hass.data[DOMAIN]["night_heating_temp"].state

        morning_time = self._hass.data[DOMAIN]["morning_time_time"].time

        if boiler_water_temp >= preheat_temp:
            _LOGGER.debug("SNPH: Water is already heated to the desired temperature")
            self._hass.data[DOMAIN]["night_heating_canceled"] = True
            self._hass.data[DOMAIN]["night_heating_event"] = async_track_time_change(
                self._hass, self._end_pre_heating, hour=morning_time.hour, minute=morning_time.minute, second=0
            )

            # Remove the planned heating
            self._hass.data[DOMAIN]["night_heating_planned"] = False
            return

        # Check if boiler is connected
        boiler_connection = await self._get_sensor_state(self._entry.data["boiler_state"], "string")
        if boiler_connection == "Disconnected":
            _LOGGER.warning("SNPH: Boiler is disconnected")
            self._hass.data[DOMAIN]["night_heating_canceled"] = True
            self._hass.data[DOMAIN]["night_heating_event"] = async_track_time_change(
                self._hass, self._end_pre_heating, hour=morning_time.hour, minute=morning_time.minute, second=0
            )

            # Remove the planned heating
            self._hass.data[DOMAIN]["night_heating_planned"] = False
            return

        # Start heating the water
        self._hass.data[DOMAIN]["night_preheating"] = True
        await self._boiler_power(True, preheat_temp)

        # Plan end of the night pre-heating
        self._hass.data[DOMAIN]["night_heating_event"] = async_track_time_change(
            self._hass, self._end_pre_heating, hour=morning_time.hour, minute=morning_time.minute, second=0
        )

        # Remove the planned heating
        self._hass.data[DOMAIN]["night_heating_planned"] = False

        _LOGGER.debug("SNPH: Night pre-heating started")

    async def _end_pre_heating(self, now) -> None:
        """End the night pre-heating.

        It will stop heating the water in the boiler and turn off the boiler.
        Also, it will set back the boiler's temperature to the desired temperature.

        Or it will clean cancelation if the night pre-heating was canceled.
        """

        _LOGGER.debug("EPH: End the night pre-heating")

        # Cancel the night pre-heating
        self._hass.data[DOMAIN]["night_heating_event"]()

        # Clean cancelation
        if self._hass.data[DOMAIN]["night_heating_canceled"]:
            self._hass.data[DOMAIN]["night_heating_canceled"] = False
            _LOGGER.debug("EPH: Night pre-heating canceled")
            return

        # Turn off the boiler
        await self._boiler_power(False)

        # Remove the planned heating
        self._hass.data[DOMAIN]["night_preheating"] = False

        _LOGGER.debug("EPH: Night pre-heating ended")

    async def _get_sensor_state(self, entity_id, type=None) -> str | float | int | None:
        """Get the state of the sensor.

        Args:
            entity_id: Entity ID of the sensor
            type: Type of the state (string, float, int)

        Return:
            ret: State of the sensor

        """

        _LOGGER.debug("GSS: Getting the state of the sensor %s", entity_id)

        if type == "string":
            ret = str(self._hass.states.get(entity_id).state)
        elif type == "float":
            ret = float(self._hass.states.get(entity_id).state)
        elif type == "int":
            ret = int(self._hass.states.get(entity_id).state)
        else:
            ret = self._hass.states.get(entity_id).state

        return ret

    async def _get_sensor_history(
        self, entity_id, s_time=None, mins: int = 0, secs: int = 0, min_val: bool = False
    ) -> float | None:
        """Get the mean value of the sensor history, calculated from the last X minutes.

        If s_time is provided, the history will be calculated from that time.

        Args:
            entity_id: Entity ID of the sensor
            s_time: Specific time to calculate the history from
            mins: Number of minutes to go back in history
            secs: Number of seconds to go back in history
            min_val: If it is set, minimum value of the sensor history will be returned

        Return:
            mean_value | min_value: Mean value of the sensor history or minimum value of the sensor history

        Source: https://www.home-assistant.io/integrations/history/

        """

        _LOGGER.debug("GSH: Getting the sensor history of %s", entity_id)

        def _is_float(value):
            """Check if string can be converted to float."""
            try:
                float(value)
                return True
            except ValueError:
                return False

        if s_time:
            s_time = s_time.replace(tzinfo=dt_util.UTC)
            start_time = s_time - timedelta(minutes=mins, seconds=secs)
            end_time = s_time
        else:
            start_time = dt_util.utcnow() - timedelta(minutes=mins, seconds=secs)
            end_time = dt_util.utcnow()

        _LOGGER.debug("GSH: Start time %s, End time %s", start_time, end_time)

        sensor_history = await self._hass.async_add_executor_job(
            lambda: history.get_significant_states(
                self._hass, start_time, end_time, [entity_id], include_start_time_state=True
            )
        )

        sensor_history = sensor_history.get(entity_id)

        # Process the history data
        if sensor_history:
            # Get all states from sensor history to list
            states = [state.state for state in sensor_history]
            # Convert only numeric states to floats
            numeric_states = [float(s) for s in states if s.isdigit() or _is_float(s)]

            # Calculate the mean value
            if numeric_states:
                if min_val:
                    # Return min value
                    min_value = min(numeric_states)
                    _LOGGER.debug("GSH: Min value %s", min_value)
                    return round(min_value, 2)

                # Return mean value
                mean_value = sum(numeric_states) / len(numeric_states)
                _LOGGER.debug("GSH: Mean value %s", mean_value)
                return round(mean_value, 2)

        return None

    async def _calculate_boiler_heat(
        self, boiler_power: int, boiler_volume: int, water_temp: float, temp_to_heat: int
    ) -> tuple[float, float]:
        """Calculate the power and time to heat the water in the boiler.

        Formula:
            Pt = (4.186 × L × dT ) ÷ 3600
            Pt_time = Pt / Power
        Where:
            Pt = Thermal power in kWh
            4.2 = Specific heat of water in kJ/kg°C
            L = Volume of water in litres
            dT = Temperature increase in °C
            3600 = Conversion factor from kJ to kWh (1kWh = 3600 kJ)
            Pt_time = Time to heat the water in hours
            Power = Power of the boiler in kW
        Source: https://sciencing.com/calculate-temperature-btu-6402970.html

        Args:
            boiler_power: Power of the boiler in W
            boiler_volume: Volume of boiler water in litres
            water_temp: Current temperature of the water in the boiler in °C
            temp_to_heat: Temperature to heat the water to in °C

        Returns:
            Pt: Thermal power in kWh
            Pt_time: Time to heat the water in minutes

        """
        _LOGGER.debug(
            "CBH: Calculating the boiler power %s %s %s %s", boiler_power, boiler_volume, water_temp, temp_to_heat
        )

        # Check if boiler volume or power is 0
        if not boiler_volume or not boiler_power:
            _LOGGER.error("Boiler volume or power is 0")
            return 0, 0

        # If the water is already heated to the desired temperature
        if water_temp >= temp_to_heat:
            return 0, 0

        Pt = (4.186 * boiler_volume * (temp_to_heat - water_temp)) / 3600  # Thermal power in kWh
        Pt_time = Pt / (boiler_power / 1000) * 60  # Time to heat the water in minutes

        return round(Pt, 2), round(Pt_time, 2)

    async def _calculate_battery_energy(
        self, battery_capacity: int, battery_soc: int, battery_to_charge: int = 80
    ) -> float:
        """Calculate the energy needed to charge the battery from the current state of charge to the desired state of charge.

        Args:
            battery_capacity: Capacity of the battery in Wh
            battery_soc: Current state of charge of the battery in %
            battery_to_charge: Desired state of charge of the battery in %

        Returns:
            energy: Energy needed to charge the battery in kWh

        """

        _LOGGER.debug("CBE: Calculating the battery energy %s %s %s", battery_capacity, battery_soc, battery_to_charge)

        # Check if battery capacity is 0
        if not battery_capacity:
            _LOGGER.error("Battery capacity is 0")
            return 0

        # If battery is already charged to the desired state of charge
        if battery_soc >= battery_to_charge:
            return 0

        # Energy needed to charge the battery in kWh
        energy = battery_capacity / 1000 * (battery_to_charge - battery_soc) / 100

        return round(energy, 2)

    async def _boiler_power(self, power, temp=None) -> None:
        """Change the state and temperature of the boiler."""

        _LOGGER.debug("BPower: Changing the state of the boiler %s %s", power, temp)

        boiler_thermostat = self._entry.data["boiler_thermostat"]  # ID of the thermostat
        boiler_mode = self._entry.data["boiler_mode"]  # ID of the mode selector

        # Turn on boiler with the desired temperature
        if power:
            # Set temperature
            await self._hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": boiler_thermostat, "temperature": temp},
                blocking=True,
            )

            # Set mode to heat ("MANUAL")
            await self._hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": boiler_mode, "option": "MANUAL"},
                blocking=True,
            )

            await self.debug_value(1, "BOILER ON")  # TODO: REMOVE

        # Turn off the boiler
        else:
            # Set mode to off ("ANTIFREEZE")
            await self._hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": boiler_mode, "option": "ANTIFREEZE"},
                blocking=True,
            )

            await self.debug_value(1, "BOILER OFF")  # TODO: REMOVE

    @callback
    async def grid_lost_handler(self, event) -> None:
        """Handle the grid lost state."""

        # Check if MQTT lost connection (Solar through automatic configuration)
        # If so, MQTT will handle this after 30s
        if self._entry.data["solar_conf_mode"] == "automatic" and not self._hass.data[DOMAIN]["mqtt_connected"]:
            return

        old_data = None
        new_data = None

        if event.data["old_state"] is not None:
            old_data = event.data["old_state"].state
        if event.data["new_state"] is not None:
            new_data = event.data["new_state"].state

        _LOGGER.debug("Grid lost handler - %s / %s", old_data, new_data)

        if new_data == "1":
            _LOGGER.warning("Grid lost")
            await self._hass.data[DOMAIN]["manager_status_select"].async_select_option("Off")
            self._hass.data[DOMAIN]["manager_status_sensor"].set_state("Off - Warning (Grid Lost)")
        elif new_data not in ["0", "1"]:
            _LOGGER.warning("Grid unknown")
            await self._hass.data[DOMAIN]["manager_status_select"].async_select_option("Off")
            self._hass.data[DOMAIN]["manager_status_sensor"].set_state("Off - Warning (Grid Unknown)")
        else:
            _LOGGER.warning("Grid back")

    # TODO: REMOVE
    async def debug_value(self, sensor, value):
        """Debug text."""

        if sensor == 1:
            await self._hass.services.async_call(
                "input_text",
                "set_value",
                {"entity_id": self._entry.data["debug_1"], "value": value},
                blocking=True,
            )
        elif sensor == 2:
            await self._hass.services.async_call(
                "input_text",
                "set_value",
                {"entity_id": self._entry.data["debug_2"], "value": value},
                blocking=True,
            )
