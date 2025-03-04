"""The babybuddy sensor integration."""
from __future__ import annotations

from asyncio import TimeoutError
from datetime import timedelta
from homeassistant.helpers.entity_registry import EntityRegistry
from homeassistant.helpers.device_registry import (
    DeviceRegistry,
    async_entries_for_config_entry,
)
import logging
from typing import Any, Tuple

from aiohttp.client_exceptions import ClientError, ClientResponseError
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ID,
    CONF_API_KEY,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SSL,
    HTTP_FORBIDDEN,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .client import BabyBuddyClient
from .const import (
    ATTR_BIRTH_DATE,
    ATTR_CHILDREN,
    ATTR_COUNT,
    ATTR_FIRST_NAME,
    ATTR_LAST_NAME,
    ATTR_RESULTS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
    SENSOR_TYPES,
)
from .errors import AuthorizationError, ConnectError

_LOGGER = logging.getLogger(__name__)

SERVICE_ADD_CHILD_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_BIRTH_DATE, default=dt_util.now().date()): cv.date,
        vol.Required(ATTR_FIRST_NAME): cv.string,
        vol.Required(ATTR_LAST_NAME): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up the Speedtest.net component."""

    coordinator = BabyBuddyCoordinator(hass, config_entry)
    await coordinator.async_setup()
    await coordinator.async_refresh()

    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = coordinator

    hass.config_entries.async_setup_platforms(config_entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload SpeedTest Entry from config_entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    )
    if unload_ok:
        hass.data[DOMAIN].pop(config_entry.entry_id)
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, "add_child")

    return unload_ok


class BabyBuddyCoordinator(DataUpdateCoordinator):
    """Coordinate retrieving and updating data from Baby Buddy."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the BabyBuddyData object."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_method=self.async_update,
            update_interval=timedelta(
                seconds=config_entry.options.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                )
            ),
        )
        self.hass = hass
        self.config_entry: ConfigEntry = config_entry
        self.client: BabyBuddyClient = BabyBuddyClient(
            config_entry.data[CONF_HOST],
            config_entry.data[CONF_PORT],
            config_entry.data[CONF_API_KEY],
            hass.helpers.aiohttp_client.async_get_clientsession(),
        )
        self.child_ids: list[str] = []

    async def async_remove_deleted_childs(self) -> None:
        """Remove child device if child is removed from Babybuddy."""
        dr: DeviceRegistry = (
            await self.hass.helpers.device_registry.async_get_registry()
        )
        for device in async_entries_for_config_entry(dr, self.config_entry.entry_id):
            if next(iter(device.identifiers))[1] not in self.child_ids:
                dr.async_remove_device(device.id)

    async def async_update(
        self,
    ) -> Tuple[list[dict[str, str]], dict[int, dict[str, dict[str, str]]]]:
        """Update BabyBuddy data."""
        children_list: dict[str, Any] = {}
        child_data: dict[int, dict[str, dict[str, str]]] = {}
        try:
            children_list = await self.client.async_get(ATTR_CHILDREN)
        except ClientResponseError as err:
            if err.status == HTTP_FORBIDDEN:
                raise ConfigEntryAuthFailed from err

        except (TimeoutError, ClientError) as err:
            raise UpdateFailed(err) from err

        if children_list[ATTR_COUNT] == 0:
            raise UpdateFailed("No children found. Please add at least one child.")

        for child in children_list[ATTR_RESULTS]:
            if child[ATTR_ID] not in self.child_ids:
                self.child_ids.append(child[ATTR_ID])
            child_data.setdefault(child[ATTR_ID], {})
            for endpoint in SENSOR_TYPES:
                endpoint_data: dict = {}
                try:
                    endpoint_data = await self.client.async_get(
                        endpoint.key, f"?child={child[ATTR_ID]}&limit=1"
                    )
                except ClientResponseError as err:
                    _LOGGER.debug(
                        f"No {endpoint} found for {child[ATTR_FIRST_NAME]} {child[ATTR_LAST_NAME]}. Skipping"
                    )
                    continue
                except (TimeoutError, ClientError) as err:
                    _LOGGER.error(err)
                    continue
                data: list[dict[str, str]] = endpoint_data[ATTR_RESULTS]
                child_data[child[ATTR_ID]][endpoint.key] = data[0] if data else {}

        await self.async_remove_deleted_childs()
        return (children_list[ATTR_RESULTS], child_data)

    async def async_setup(self) -> None:
        """Set up BabyBuddy."""

        try:
            await self.client.async_connect()
        except AuthorizationError as err:
            raise ConfigEntryAuthFailed from err
        except ConnectError as err:
            raise ConfigEntryNotReady(err) from err

        async def async_add_child(call: ServiceCall) -> None:
            """Add new child."""
            data = {
                ATTR_FIRST_NAME: call.data[ATTR_FIRST_NAME],
                ATTR_LAST_NAME: call.data[ATTR_LAST_NAME],
                ATTR_BIRTH_DATE: call.data[ATTR_BIRTH_DATE],
            }
            await self.client.async_post(ATTR_CHILDREN, data)
            await self.async_request_refresh()

        self.hass.services.async_register(
            DOMAIN, "add_child", async_add_child, schema=SERVICE_ADD_CHILD_SCHEMA
        )


async def options_updated_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    hass.data[DOMAIN][entry.entry_id].update_interval = timedelta(
        seconds=entry.options[CONF_SCAN_INTERVAL]
    )
    await hass.data[DOMAIN][entry.entry_id].async_request_refresh()
