"""Constants for the TransportMe Bus Tracker integration."""

DOMAIN = "transportme"

# Config entry keys
CONF_API_BASE_URL = "api_base_url"
CONF_AUTH_TOKEN = "auth_token"
CONF_SUBSCRIPTION_ID = "subscription_id"
CONF_STOP_LAT = "stop_latitude"
CONF_STOP_LON = "stop_longitude"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_REFRESH_TOKEN = "refresh_token"

# Defaults
DEFAULT_SCAN_INTERVAL = 30  # seconds
DEFAULT_API_BASE_URL = "https://production.api2.transportme.com.au"

# Coordinator key
COORDINATOR = "coordinator"

# Sensor types
SENSOR_ETA_MINUTES = "eta_minutes"
SENSOR_DISTANCE_KM = "distance_km"
SENSOR_SPEED = "speed"
SENSOR_STATUS = "status"
SENSOR_LAST_UPDATED = "last_updated"

# Attribute names
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_SPEED = "speed"
ATTR_HEADING = "heading"
ATTR_VEHICLE_ID = "vehicle_id"
ATTR_ROUTE = "route"
ATTR_STATUS = "status"
ATTR_LAST_UPDATE = "last_update"

# Platforms
PLATFORMS = ["device_tracker", "sensor"]
