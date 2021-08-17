"""Baby buddy client class"""
from __future__ import annotations

import logging
from asyncio import TimeoutError
from datetime import datetime, time
from typing import Any

import async_timeout
import homeassistant.util.dt as dt_util
from aiohttp.client import ClientSession
from aiohttp.client_exceptions import ClientError, ClientResponseError
from homeassistant.const import HTTP_CREATED, HTTP_OK

from .errors import AuthorizationError, ConnectError, ValidationError

_LOGGER = logging.getLogger(__name__)


class BabyBuddyClient:
    """Class for Babybuddy api interface."""

    def __init__(
        self, host: str, port: int, api_key: str, session: ClientSession
    ) -> None:
        """Initialize the client."""
        self.headers = {"Authorization": f"Token {api_key}"}
        self.url = f"{host}:{port}"
        self.session = session
        self.endpoints: dict[str, str] = {}

    async def async_get(
        self, endpoint: str | None = None, entry: str | None = None
    ) -> Any:
        """GET request to babybuddy api."""
        url = f"{self.url}/api/"
        if endpoint:
            url = self.endpoints[endpoint]
            if entry:
                url = f"{url}{entry}"
        with async_timeout.timeout(10):
            resp = await self.session.get(
                url=url,
                headers=self.headers,
                raise_for_status=True,
            )

        return await resp.json()

    async def async_post(self, endpoint: str, data: dict[str, Any]) -> None:
        """POST request to babybuddy api."""
        _LOGGER.debug("POST data: %s", data)
        try:
            with async_timeout.timeout(10):
                resp = await self.session.post(
                    self.endpoints[endpoint],
                    headers=self.headers,
                    data=data,
                )
        except (TimeoutError, ClientError) as err:
            _LOGGER.error(err)

        if resp.status != HTTP_CREATED:
            error = await resp.json()
            _LOGGER.error(f"Could not create {endpoint}. error: {error}")

    async def async_patch(
        self, endpoint: str, entry: str, data: dict[str, str]
    ) -> None:
        try:
            with async_timeout.timeout(10):
                resp = await self.session.patch(
                    f"{self.endpoints[endpoint]}{entry}/",
                    headers=self.headers,
                    data=data,
                )
        except (TimeoutError, ClientError) as err:
            _LOGGER.error(err)

        if resp.status != HTTP_OK:
            error = await resp.json()
            _LOGGER.error(f"Could not update {endpoint}/{entry}. error: {error}")

    async def async_delete(self, endpoint: str, entry: str) -> None:
        try:
            with async_timeout.timeout(10):
                resp = await self.session.delete(
                    f"{self.endpoints[endpoint]}{entry}/",
                    headers=self.headers,
                )
        except (TimeoutError, ClientError) as err:
            _LOGGER.error(err)

        if resp.status != 204:
            error = await resp.json()
            _LOGGER.error(f"Could not delete {endpoint}/{entry}. error: {error}")

    async def async_connect(self) -> None:
        """Check connection to Babybuddy api."""
        try:
            self.endpoints = await self.async_get()
        except ClientResponseError as err:
            raise AuthorizationError from err
        except (TimeoutError, ClientError) as err:
            raise ConnectError(err) from err


def get_datetime_from_time(value: datetime | time) -> datetime:
    """Return datetime for start/end/time service fields."""
    if isinstance(value, time):
        value = datetime.combine(dt_util.now().date(), value, dt_util.DEFAULT_TIME_ZONE)
    if isinstance(value, datetime):
        value = value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    if value > dt_util.now():
        raise ValidationError("Time cannot be in the future.")
    return value
