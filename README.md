
# PV Water Heating Manager (HA component)

The component adds the ability to automatically control the boiler to heat hot water, using the surplus from the PV panels and covering the fluctuations in generation from the batteries.

The component has fully automatic and manual (semi-automatic) control. In automatic control, the system relies on the generation forecast and accordingly triggers the pre-heating of the water and the following heating during the day. Manual control always triggers pre-heating to the set temperature and further heats from the available surplus.

The [ESPHome component for Dražice OKHE smart water heater](https://github.com/LubosD/esphome-smartboiler) or the same entities that the component adds is required for proper functioning.

The component is focused only on single-phase systems and works best with [Venus OS](https://github.com/victronenergy/venus) and the mentioned boiler control component.
## Features
- Manual mode - Heating during the day using surplus
- Automatic mode - Fully automatic control based on surplus
- Boiler temperature setting - Required water temperature
- Night pre-heating on/off
    - Night pre-heat temperature setting
    - Morning time setting (When to stop pre-heating)
- Today's and tomorrow's generation forecast (Only works with VRM API)
*If the VRM API is not used, no predictions will be displayed and only manual mode will be available.*

The only difference between the automatic and manual mode is that the manual mode always turns on the night pre-heat at the scheduled time and temperature. Automatic only turns it on when it predicts enough surplus during the day.

If the pre-heating is switched on, it schedules itself for a certain time so that by "morning time" the water is already heated to the set temperature.
## Installation
Proper setup of the VenusOS, MQTT and ESPHome component to control the boiler is required before installation.

### VenusOS setup
You need to set a static IP address. Then you need to enable MQTT in **Settings -> Services**.
- MQTT on LAN (SSL)
- MQTT on LAN (Plaintext)
These two settings must be enabled.

> [!NOTE]
> VenusOS Firmware version v2.87

### ESPHome component to control the boiler
You must add the boiler using [ESPHome component for Dražice OKHE smart water heater](https://github.com/LubosD/esphome-smartboiler) to Home Assistant.

> [!NOTE]
> Version from 12. Nov 2023 (Commit 28ef062)

### VRM API
Generate an Acess token in the VRM portal.
After logging into [VRM portal](https://vrm.victronenergy.com/login), you will go to `Preferences -> Integration -> Acess tokens` and generate a new token.

Also make a note of the installation ID, you can find it in the url after **installation** (*vrm.victronenergy.com/installation/<installation_id>/dashboard*)

*\*This is not required*

### MQTT on Home Assistant
In Home Assistant under `Settings -> Devices & services` add the MQTT integration.

As broker ip, use VenusOS ip, port 1883 then under advanced options it is necessary to enable **Enable discovery** with **homeassistant** prefix.

### PV Water Heating Manager
Just copy the files directly into the homeassistant custom_components folder

```
custom_components/pv_water_heating_manager/
```
Then go to `Settings -> Devices & services` and add ***PV Water Heating Manager*** using the **ADD INTEGRATION** button. The whole installation is described in the UI.

But you can choose between automatic and manual installation:
- Choose automatic if you use the components mentioned above (VenusOS and ESPHome component), component is primarily designed to work with them. - **Recommended, manual may not work properly**.
- In manual installation you have to select all entities yourself and they **must have** the same parameters as the entities created with VenusOS and ESPHome.

*\*Manual configuration is in the experimental phase.*

After selecting the automatic option, several configuration windows will appear:

**Boiler settings:**
- Boiler device setuped by ESPHome
- Boiler power in Watts
- Boiler volume in Liters

**Solar system settings:**
- Venus MQTT topic (MAC address of installation, it can be found after connecting to VenusOS broker as "<IP>/N/<topic>")
- VRM installation ID (not required)
- VRM token (not required)

**Additional information**
- Battery capacity in Wh
- Battery state of charge top threshold
- Battery state of charge bottom threshold
- Temperature variable - Used to calculate the energy required to heat the water in the boiler. This value is subtracted from the set heating value and it is calculated whether the boiler can be heated (basically the minimum temperature).
- Grid threshold - Acts as a safety feature, if for some reason more energy is taken from the grid than this value, the boiler will turn off.
- Manager updates - How often the manager will update.
Battery SOC top and bottom thresholds are used to cover pv generation fluctuations. Battery charging takes priority over the boiler and batteries are kept charged at these values.

## Usage
After successful installation, all necessary entities will be added to Home Assistant.

- Entities from the solar system have the Venus prefix.

After adding all the manager entities to the dashboard, it will look something like this:
![Manager image](https://github.com/FNewel/PV-Water-Heating-Manager/blob/main/images/manager.png "Manager")

#### Controls
- Manager Status - Shows the current status of the manager (for example: network outage, errors ..)
- Manager Control - Used to control the manager - to turn it on/off (Off, Manual, Automatic)
- Heating Temperature - Used to set the boiler temperature setting
- Nigh pre-heating - Used to turn on the night pre-heating
- Night Heating Temperature - Used to set the night pre-heating temperature
- Morning Time - When the night pre-heating should be stopped
- PV Forecasts - Shows today's and tomorrow's solar panel generation forecast

MQTT data are updated as soon as there is a change in the broker. When the manager is turned off, the data from the solar system are still updated, only the manager is turned off.

## License

[MIT](https://github.com/FNewel/PV-Water-Heating-Manager/blob/main/LICENSE)
