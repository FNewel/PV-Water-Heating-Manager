"""Constants for the PV Water Heating Manager integration."""

DOMAIN = "pv_water_heating_manager"

# Topics to discovery and subscribe on MQTT broker
TOPICS = {
    # Grid Loads
    "Ac/Grid/L1/Power": {
        "type": "system",
        "name": "Grid L1",
        "unit_of_measurement": "W",
        "state_class": "measurement",
        "value_template": "{% if value_json.value != None %}{{ value_json.value | round(0) }}{% else %}None{% endif %}",
    },
    "Ac/Grid/L2/Power": {
        "type": "system",
        "name": "Grid L2",
        "unit_of_measurement": "W",
        "state_class": "measurement",
        "value_template": "{% if value_json.value != None %}{{ value_json.value | round(0) }}{% else %}None{% endif %}",
    },
    "Ac/Grid/L3/Power": {
        "type": "system",
        "name": "Grid L3",
        "unit_of_measurement": "W",
        "state_class": "measurement",
        "value_template": "{% if value_json.value != None %}{{ value_json.value | round(0) }}{% else %}None{% endif %}",
    },
    # AC Loads
    "Ac/ConsumptionOnInput/L1/Power": {
        "type": "system",
        "name": "Load L1",
        "unit_of_measurement": "W",
        "state_class": "measurement",
        "value_template": "{% if value_json.value != None %}{{ value_json.value | round(0) }}{% else %}None{% endif %}",
    },
    "Ac/ConsumptionOnInput/L2/Power": {
        "type": "system",
        "name": "Load L2",
        "unit_of_measurement": "W",
        "state_class": "measurement",
        "value_template": "{% if value_json.value != None %}{{ value_json.value | round(0) }}{% else %}None{% endif %}",
    },
    "Ac/ConsumptionOnInput/L3/Power": {
        "type": "system",
        "name": "Load L3",
        "unit_of_measurement": "W",
        "state_class": "measurement",
        "value_template": "{% if value_json.value != None %}{{ value_json.value | round(0) }}{% else %}None{% endif %}",
    },
    # Critical Load
    "Ac/ConsumptionOnOutput": {
        "type": "critical_load",
        "name": "Critical Load",
        "unit_of_measurement": "W",
        "state_class": "measurement",
        "value_template": "{% if value_json.value != None %}{{ value_json.value | round(0) }}{% else %}None{% endif %}",
    },
    # Battery ESS
    "Settings/CGwacs/BatteryLife/State": {
        "type": "battery_ess",
        "name": "Battery ESS",
        "unit_of_measurement": "",
        "state_class": "",
        "value_template": "{{ value_json.value }}",
    },
    # Battery Power
    "Power": {
        "type": "battery_power",
        "name": "Battery Power",
        "unit_of_measurement": "W",
        "state_class": "measurement",
        "value_template": "{% if value_json.value != None %}{{ value_json.value | round(0) }}{% else %}None{% endif %}",
    },
    # Battery SOC
    "Soc": {
        "type": "battery_soc",
        "name": "Battery SOC",
        "unit_of_measurement": "%",
        "state_class": "",
        "value_template": "{% if value_json.value != None %}{{ value_json.value | round(0) }}{% else %}None{% endif %}",
    },
    # PV Power
    "Dc/Pv/Power": {
        "type": "system",
        "name": "PV Power",
        "unit_of_measurement": "W",
        "state_class": "measurement",
        "value_template": "{% if value_json.value != None %}{{ value_json.value | round(0) }}{% else %}None{% endif %}",
    },
    # System State (Discharging, Bulk Charging, etc.)
    "SystemState/State": {
        "type": "system",
        "name": "System State",
        "unit_of_measurement": "",
        "state_class": "",
        "value_template": "{{ value_json.value }}",
    },
    # Grid State (Grid available or Grid lost)
    "Alarms/GridLost": {
        "type": "grid_lost",
        "name": "Grid Lost",
        "unit_of_measurement": "",
        "state_class": "",
        "value_template": "{{ value_json.value }}",
    },
}


# Required entities by automatic boiler setup
BOILER_REQ_ENTITIES = [
    "Water heater heat",
    "Water heater temp1",
    "Water heater temp2",
    "Water heater state",
    "Water heater thermostat",
    "Water heater mode",
]
