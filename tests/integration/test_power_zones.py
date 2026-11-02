"""get_power_zones: FTP and each zone's range per sport.

The payload follows the live reply's shape; the values are synthetic.
"""
import json
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import user_profile
from garmin_mcp.client_resolver import set_global_client

ZONES = [
    {"sport": "CYCLING", "functionalThresholdPower": 200.0, "zone1Floor": 0.0, "zone2Floor": 110.0,
     "zone3Floor": 150.0, "zone4Floor": 180.0, "zone5Floor": 210.0, "zone6Floor": 240.0,
     "zone7Floor": 300.0, "userLocalTime": None},
    {"sport": "RUNNING", "functionalThresholdPower": 300.0, "zone1Floor": 195.0, "zone2Floor": 240.0,
     "zone3Floor": 270.0, "zone4Floor": 300.0, "zone5Floor": 345.0, "zone6Floor": 0.0,
     "zone7Floor": 0.0, "userLocalTime": None},
]


@pytest.fixture
def app():
    client = Mock()
    client.get_power_zones.return_value = ZONES
    user_profile.configure(client)
    set_global_client(client)
    yield user_profile.register_tools(FastMCP("zones"))
    set_global_client(None)


async def _call(app, args):
    return (await app.call_tool("get_power_zones", args))[0][0].text


@pytest.mark.asyncio
async def test_running_has_five_zones_from_the_floors_garmin_uses(app):
    payload = json.loads(await _call(app, {"sport": "running"}))

    running = payload["power_zones"][0]
    assert running["sport"] == "RUNNING" and running["ftp_watts"] == 300.0
    assert [z["zone"] for z in running["zones"]] == [1, 2, 3, 4, 5]
    assert running["zones"][1] == {"zone": 2, "from_watts": 240.0, "to_watts": 270.0, "from_percent_ftp": 80}
    # The top zone has no ceiling; the unused floors (0) are not zones.
    assert running["zones"][-1] == {"zone": 5, "from_watts": 345.0, "to_watts": None, "from_percent_ftp": 115}


@pytest.mark.asyncio
async def test_cycling_keeps_seven_zones_starting_at_zero(app):
    payload = json.loads(await _call(app, {"sport": "cycling"}))

    zones = payload["power_zones"][0]["zones"]
    assert len(zones) == 7
    assert zones[0] == {"zone": 1, "from_watts": 0.0, "to_watts": 110.0, "from_percent_ftp": 0}


@pytest.mark.asyncio
async def test_every_sport_without_a_filter(app):
    payload = json.loads(await _call(app, {}))

    assert [z["sport"] for z in payload["power_zones"]] == ["CYCLING", "RUNNING"]


@pytest.mark.asyncio
async def test_an_unknown_sport_is_said_plainly(app):
    assert await _call(app, {"sport": "golf"}) == "No power zones found for golf."
