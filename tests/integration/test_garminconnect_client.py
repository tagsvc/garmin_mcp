"""The fork's calls through a real garminconnect client, offline.

Most tests replace the Garmin object with a mock, so they cannot see a change
in how the library builds a request, logs in or stores tokens. Here the Garmin
object and its client are real, and the offline_garmin fixture blocks the
network and answers the API requests (tests/offline_garmin.py). Every value is
synthetic.
"""
import ast
import base64
import json
import os
import datetime
import stat
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from garminconnect import Garmin, GarminConnectTooManyRequestsError
from mcp.server.fastmcp import FastMCP

import garmin_mcp
from garmin_mcp import (
    auth_cli, auth_tools, data_management, health_snapshots, weight_management, workouts,
)
from garmin_mcp.client_resolver import set_global_client
from tests.offline_garmin import TOKENS

# Every test here runs offline, which is what lets this module log in at all
# (tests/unit/test_live_auth_is_excluded_from_ci.py).
pytestmark = pytest.mark.usefixtures("offline_garmin")


def _store(path: Path, tokens=TOKENS) -> Path:
    path.mkdir(mode=0o700)
    token_file = path / "garmin_tokens.json"
    token_file.write_text(json.dumps(tokens))
    os.chmod(token_file, 0o600)
    return path


def _signed_in(tmp_path) -> Garmin:
    garmin = Garmin()
    garmin.login(str(_store(tmp_path / "tokens")))
    return garmin


# ─── tokens ───────────────────────────────────────────────────────────────


def test_saved_tokens_load_and_sign_in(offline_garmin, tmp_path):
    """Token files as production holds them load, and their token signs requests."""
    garmin = _signed_in(tmp_path)

    assert garmin.display_name == "test-runner"
    assert garmin.client.di_refresh_token == "synthetic-refresh-token"
    profile = offline_garmin.to("/userprofile-service/socialProfile")
    assert profile and profile[0]["headers"]["Authorization"] == f"Bearer {TOKENS['di_token']}"


def test_dumped_tokens_are_owner_only_and_load_again(offline_garmin, tmp_path):
    garmin = _signed_in(tmp_path)
    out = tmp_path / "dumped"

    garmin.client.dump(str(out))

    token_file = out / "garmin_tokens.json"
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    saved = json.loads(token_file.read_text())
    assert {key: saved.get(key) for key in TOKENS} == TOKENS
    again = Garmin()
    again.login(str(out))
    assert again.display_name == "test-runner"


# ─── MFA: the tokens are saved before anything else can fail ──────────────


def _mfa_then_profile_failure(monkeypatch):
    """Garmin asks for a code; the code mints tokens; loading the profile then fails.

    Garmin.resume_login finishes the exchange and then loads the profile, and
    some garminconnect releases raise when that load fails (a 429 is the usual
    cause, right after a login). The client-level resume_login only finishes
    the exchange.
    """
    client_type = type(Garmin().client)

    def login(self, email, password, prompt_mfa=None, return_on_mfa=False):
        return "needs_mfa", {"state": "synthetic"}

    def resume_login(self, client_state, mfa_code):
        assert mfa_code == "123456"
        self.loads(json.dumps(TOKENS))
        return None, None

    def resume_and_load_profile(self, client_state, mfa_code):
        self.client.resume_login(client_state, mfa_code)
        raise GarminConnectTooManyRequestsError("429 loading the profile")

    monkeypatch.setattr(client_type, "login", login)
    monkeypatch.setattr(client_type, "resume_login", resume_login)
    monkeypatch.setattr(Garmin, "resume_login", resume_and_load_profile)


def test_auth_cli_saves_mfa_tokens_before_loading_the_profile(offline_garmin, monkeypatch, tmp_path):
    _mfa_then_profile_failure(monkeypatch)
    monkeypatch.setattr(auth_cli, "get_credentials", lambda: ("user@example.invalid", "unused-test-password"))
    monkeypatch.setattr(auth_cli, "get_mfa", lambda: "123456")
    tokens = tmp_path / "tokens"

    assert auth_cli.authenticate(str(tokens), str(tmp_path / "tokens_base64"), force_reauth=True) is True

    saved = json.loads((tokens / "garmin_tokens.json").read_text())
    assert saved["di_refresh_token"] == "synthetic-refresh-token"
    # The verification login loaded the profile from the saved tokens.
    assert offline_garmin.to("/userprofile-service/socialProfile")


def test_server_saves_mfa_tokens_before_loading_the_profile(offline_garmin, monkeypatch, tmp_path):
    _mfa_then_profile_failure(monkeypatch)
    monkeypatch.setattr(garmin_mcp, "get_mfa", lambda: "123456")
    tokens = tmp_path / "tokens"

    garmin = garmin_mcp.init_api("user@example.invalid", "unused-test-password",
                                 tokenstore=str(tokens), tokenstore_base64=str(tmp_path / "b64"))

    assert garmin is not None and garmin.display_name == "test-runner"
    saved = json.loads((tokens / "garmin_tokens.json").read_text())
    assert saved["di_refresh_token"] == "synthetic-refresh-token"


@pytest.mark.asyncio
async def test_login_tool_asks_for_the_code_when_garmin_wants_one(offline_garmin, monkeypatch, tmp_path):
    """Garmin.login wraps most errors in its own; the MFA signal must come through."""
    monkeypatch.setenv("GARMIN_DISABLE_LOCAL_PROMPTS", "1")
    for name in ("GARMIN_MFA_CODE", "GARMIN_OTP"):
        monkeypatch.delenv(name, raising=False)

    def login(self, email, password, prompt_mfa=None, return_on_mfa=False):
        prompt_mfa()
        raise AssertionError("the prompt had no code to give")

    monkeypatch.setattr(type(Garmin().client), "login", login)
    auth_tools.configure(lambda client: None)
    app = auth_tools.register_tools(FastMCP("auth"))

    result = json.loads(await _call(app, "login_to_garmin", {
        "email": "user@example.invalid", "password": "unused-test-password",
        "token_path": str(tmp_path / "tokens"), "force_reauth": True,
    }))

    assert result["status"] == "mfa_required"
    assert not (tmp_path / "tokens" / "garmin_tokens.json").exists()


def _login_tool(monkeypatch):
    """login_to_garmin, where Garmin asks for a code and the right one mints tokens."""
    def login(self, email, password, prompt_mfa=None, return_on_mfa=False):
        assert prompt_mfa() == "123456"
        self.loads(json.dumps(TOKENS))
        return None, None

    monkeypatch.setattr(type(Garmin().client), "login", login)
    activated = []
    auth_tools.configure(activated.append)
    return auth_tools.register_tools(FastMCP("auth")), activated


def _login_args(tmp_path):
    return {"email": "user@example.invalid", "password": "unused-test-password",
            "otp_code": "123456", "force_reauth": True,
            "token_path": str(tmp_path / "tokens"),
            "token_base64_path": str(tmp_path / "tokens_base64")}


@pytest.mark.asyncio
async def test_login_tool_saves_the_tokens_and_signs_in(offline_garmin, monkeypatch, tmp_path):
    app, activated = _login_tool(monkeypatch)

    result = json.loads(await _call(app, "login_to_garmin", _login_args(tmp_path)))

    assert result["status"] == "authenticated"
    assert [garmin.display_name for garmin in activated] == ["test-runner"]
    saved = json.loads((tmp_path / "tokens" / "garmin_tokens.json").read_text())
    assert saved["di_refresh_token"] == "synthetic-refresh-token"
    # The optional copy is base64, like the other two writers', and owner-only.
    copy = tmp_path / "tokens_base64"
    assert json.loads(base64.b64decode(copy.read_text()))["di_refresh_token"] == "synthetic-refresh-token"
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_login_tool_keeps_the_tokens_when_the_profile_load_fails(offline_garmin, monkeypatch, tmp_path):
    """A 429 straight after a correct code must not cost the user the tokens."""
    app, activated = _login_tool(monkeypatch)
    offline_garmin.routes["/userprofile-service/socialProfile"] = 429
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    result = json.loads(await _call(app, "login_to_garmin", _login_args(tmp_path)))

    assert result["status"] == "tokens_saved"
    assert "Restart the MCP server" in result["message"]
    assert activated == []
    saved = json.loads((tmp_path / "tokens" / "garmin_tokens.json").read_text())
    assert saved["di_refresh_token"] == "synthetic-refresh-token"


# ─── Garmin Coach: queries travel as params ───────────────────────────────

PLAN_DETAIL = {"athletePlanId": 11, "quit": "2026-10-01T12:00:00.0",
               "athleteRace": {"raceName": "Test 5K", "raceDay": "2026-12-06"}}
CALENDAR = [{"scheduledWorkoutDate": "2026-09-14", "workoutId": 21, "workoutName": "Base",
             "workoutType": "running"}]


def _app(garmin):
    workouts.configure(garmin)
    set_global_client(garmin)
    return workouts.register_tools(FastMCP("garminconnect client"))


async def _call(app, name, args):
    return (await app.call_tool(name, args))[0][0].text


@pytest.fixture
def coach(offline_garmin, tmp_path):
    offline_garmin.routes.update({
        "/atp-api/atp/athlete/plan": PLAN_DETAIL,
        "/atp-api/atp/athlete/calendar": CALENDAR,
        "/atp-api/atp/athlete/active": [],
        "/atp-api/atp/athlete/completed": [{"athletePlanId": 11, "planCompleted": True}],
    })
    yield _app(_signed_in(tmp_path))
    set_global_client(None)


def _no_query_in_paths(api):
    for call in api.calls:
        assert "?" not in call["url"], f"query string in the path: {call['url']}"


@pytest.mark.asyncio
async def test_coach_plan_details(coach, offline_garmin):
    payload = json.loads(await _call(coach, "get_coach_plan_details", {"plan_id": 11}))

    assert payload["race_name"] == "Test 5K"
    assert offline_garmin.to("/atp-api/atp/athlete/plan")[0]["params"] == {"lang": "en", "athletePlanId": 11}
    _no_query_in_paths(offline_garmin)


@pytest.mark.asyncio
async def test_coach_plan_progress(coach, offline_garmin):
    text = await _call(coach, "get_coach_plan_progress",
                       {"plan_id": 11, "start_date": "2026-09-13", "end_date": "2026-09-19"})

    assert not text.startswith("Error"), text
    assert offline_garmin.to("/atp-api/atp/athlete/calendar")[0]["params"] == {
        "athletePlanId": 11, "startDate": "2026-09-13", "endDate": "2026-09-19", "lang": "en"}
    _no_query_in_paths(offline_garmin)


@pytest.mark.asyncio
async def test_coach_plans_reads_quit_from_the_plan_detail(coach, offline_garmin):
    """A failed detail call reads as "ended": the list has to reach the detail."""
    payload = json.loads(await _call(coach, "get_coach_plans", {}))

    assert payload["plans"][0]["status"] == "quit"
    _no_query_in_paths(offline_garmin)


# ─── timed writes: the profile's zone, not the server's clock ─────────────

NEW_YORK = ZoneInfo("America/New_York")


@pytest.fixture
def server_on_utc(monkeypatch):
    """The machine's clock in UTC, as on Railway."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def writer(offline_garmin, server_on_utc, tmp_path):
    offline_garmin.routes.update({
        "/userprofile-service/userprofile/personal-information/test-runner": {"timeZone": "America/New_York"},
        "/weight-service/user-weight": {"ok": True},
        "/bloodpressure-service/bloodpressure": {"ok": True},
    })
    garmin = _signed_in(tmp_path)
    set_global_client(garmin)
    app = FastMCP("writes")
    for module in (weight_management, data_management):
        module.configure(garmin)
        module.register_tools(app)
    yield app
    set_global_client(None)


def _sent(api, path):
    return next(call["json"] for call in api.calls if call["path"] == path and call["method"] == "POST")


def _ts(text):
    return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f")


@pytest.mark.asyncio
async def test_a_weigh_in_with_only_a_local_time_gets_gmt_from_the_profile_zone(writer, offline_garmin):
    """7:30 PM in New York is 00:30 GMT the next day, whatever the server's clock."""
    await _call(writer, "add_weigh_in_with_timestamps",
                {"weight": 80.0, "unit_key": "kg", "date_timestamp": "2026-11-02T19:30:00"})

    sent = _sent(offline_garmin, "/weight-service/user-weight")
    assert sent["dateTimestamp"] == "2026-11-02T19:30:00.000"
    assert sent["gmtTimestamp"] == "2026-11-03T00:30:00.000"


@pytest.mark.asyncio
async def test_blood_pressure_with_no_time_is_logged_at_the_profile_zones_now(writer, offline_garmin):
    before = datetime.datetime.now(NEW_YORK).replace(tzinfo=None)
    await _call(writer, "set_blood_pressure", {"systolic": 120, "diastolic": 80, "pulse": 60})
    after = datetime.datetime.now(NEW_YORK).replace(tzinfo=None)

    sent = _sent(offline_garmin, "/bloodpressure-service/bloodpressure")
    local, gmt = _ts(sent["measurementTimestampLocal"]), _ts(sent["measurementTimestampGMT"])
    assert before - datetime.timedelta(seconds=1) <= local <= after
    assert gmt - local == -NEW_YORK.utcoffset(local)


# ─── Health Snapshot and ECG: the library's requests ─────────────────────

SNAPSHOT_UUID = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def snapshots(offline_garmin, tmp_path):
    base = "/wellnessactivity-service/activity"
    offline_garmin.routes.update({
        f"{base}/summary/range/2026-10-01/2026-11-01": [{"activityUuid": {"uuid": SNAPSHOT_UUID},
                                                         "calendarDate": "2026-11-01"}],
        f"{base}/summary/2026-11-01/{SNAPSHOT_UUID}": {"activityUuid": {"uuid": SNAPSHOT_UUID}},
        f"{base}/epoch/{SNAPSHOT_UUID}": {"epochDescriptorDTOList": [], "epochArray": []},
        "/ghs/ecg/v1/ecg/list": {"ecgs": []},
    })
    garmin = _signed_in(tmp_path)
    health_snapshots.configure(garmin)
    set_global_client(garmin)
    yield health_snapshots.register_tools(FastMCP("snapshots"))
    set_global_client(None)


@pytest.mark.asyncio
async def test_health_snapshot_tools_send_the_librarys_requests(snapshots, offline_garmin):
    listed = json.loads(await _call(snapshots, "get_health_snapshots",
                                    {"start_date": "2026-10-01", "end_date": "2026-11-01"}))
    await _call(snapshots, "get_health_snapshot",
                {"snapshot_id": SNAPSHOT_UUID, "date": "2026-11-01", "include_samples": True})

    assert listed["snapshots"][0]["snapshot_id"] == SNAPSHOT_UUID
    paths = [call["path"] for call in offline_garmin.calls if "wellnessactivity" in call["path"]]
    assert paths == [
        "/wellnessactivity-service/activity/summary/range/2026-10-01/2026-11-01",
        f"/wellnessactivity-service/activity/summary/2026-11-01/{SNAPSHOT_UUID}",
        f"/wellnessactivity-service/activity/epoch/{SNAPSHOT_UUID}",
    ]


@pytest.mark.asyncio
async def test_ecg_requests_go_to_garmins_ecg_host_signed_in(snapshots, offline_garmin):
    await _call(snapshots, "get_ecg_recordings", {"start_date": "2026-10-01", "end_date": "2026-11-01"})

    call = next(c for c in offline_garmin.calls if c["path"] == "/ghs/ecg/v1/ecg/list")
    assert call["host"] == "ghs.garmin.com"
    assert call["params"]["firstDay"] == "2026-10-01" and call["params"]["lastDay"] == "2026-11-01"
    assert call["headers"]["Authorization"] == f"Bearer {TOKENS['di_token']}"


# ─── every request path in the source ─────────────────────────────────────

SRC = Path(__file__).resolve().parents[2] / "src" / "garmin_mcp"
# The remote server's own pages, which are not Garmin requests.
OWN_ROUTES = {"/login", "/login/mfa"}


def _literal(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
    return None


def test_no_request_path_in_the_source_carries_a_query_string():
    """Queries go in params: garminconnect's newer releases refuse a path with '?'."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            text = _literal(node)
            if text and text.startswith("/") and "?" in text and text.split("?")[0] not in OWN_ROUTES:
                found.append(f"{path.relative_to(SRC)}:{node.lineno}: {text}")
    assert not found, "move these queries into params:\n" + "\n".join(found)
