"""Tests for the committed fixture day.

The dashboard is being built against `fixtures/day.json` in a separate session, so the file
is an interface and not a convenience. These tests pin the numbers it derives to: if a
change to the segmenter moves them, that is a conversation to have rather than a silent
redefinition of what the other half of the project is rendering.

The expected values are the ones the day was designed to produce, written out rather than
recomputed from the fixture, so a test cannot agree with a bug by deriving its expectation
the same wrong way.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from groundtruth_server.app import create_app
from groundtruth_server.config import FIXTURE_DAY, Settings
from groundtruth_server.fixtures import load_into_store, read_batches
from groundtruth_server.models import UNCERTAIN

DAY_FROM = "2026-10-28T00:00:00Z"
DAY_TO = "2026-10-28T23:59:59Z"

TRACKED_S = 28_800.0
"""Eight hours exactly. The day was built to land on a round number so that a drift of one
sample's coverage is visible rather than lost in the noise."""

BY_CATEGORY = {
    "deep work": 8100.0,
    "doomscrolling": 7080.0,
    "gym": 3300.0,
    "research": 3300.0,
    "admin": 1800.0,
    "devotion": 1680.0,
    "communication": 1500.0,
    "entertainment": 1200.0,
    UNCERTAIN: 840.0,
}

BY_PROCESS = {
    "chrome.exe": 12480.0,
    "code.exe": 6000.0,
    "(off-screen)": 3300.0,
    "windowsterminal.exe": 2100.0,
    "outlook.exe": 1800.0,
    "slack.exe": 1500.0,
    "vlc.exe": 1200.0,
    "msedge.exe": 420.0,
}


@pytest.fixture
def loaded(store) -> TestClient:
    """A server with the fixture day loaded."""
    accepted, duplicates = load_into_store(store, read_batches(FIXTURE_DAY))
    assert (accepted, duplicates) == (480, 0)
    with TestClient(create_app(store=store, settings=Settings(db_path=store.path))) as client:
        yield client


def window(client: TestClient, path: str, **params) -> dict:
    response = client.get(path, params={"from": DAY_FROM, "to": DAY_TO, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_fixture_is_a_valid_batch_of_post_events_bodies():
    """The file's shape is the contract's request shape, so a dashboard can replay it."""
    batches = read_batches(FIXTURE_DAY)
    assert {batch.device_id for batch in batches} == {"desk-win-01", "phone-android-01"}
    assert sum(len(batch.events) for batch in batches) == 480


def test_loading_the_fixture_twice_changes_nothing(store):
    """Seeding a demo database is safe to re-run."""
    batches = read_batches(FIXTURE_DAY)
    assert load_into_store(store, batches) == (480, 0)
    assert load_into_store(store, batches) == (0, 480)
    assert store.count_events() == 480


def test_the_day_derives_twelve_sessions(loaded):
    sessions = window(loaded, "/sessions")["sessions"]
    assert len(sessions) == 12
    assert [s["category"] for s in sessions] == [
        "devotion",
        "gym",
        "deep work",
        UNCERTAIN,
        "research",
        "communication",
        UNCERTAIN,
        "doomscrolling",
        "doomscrolling",
        "admin",
        "deep work",
        "entertainment",
    ]


def test_the_day_totals_eight_tracked_hours(loaded):
    body = window(loaded, "/summary")
    assert body["total_tracked_s"] == TRACKED_S
    assert {g["key"]: g["duration_s"] for g in body["groups"]} == BY_CATEGORY


def test_the_naive_view_differs_visibly_from_the_real_one(loaded):
    """The headline comparison. Chrome is 43% of the day and means five different things."""
    body = window(loaded, "/summary")
    naive = {g["key"]: g["duration_s"] for g in body["naive_by_process"]}

    assert naive == BY_PROCESS
    assert naive["chrome.exe"] == 12480.0
    assert body["naive_by_process"][0]["share"] == pytest.approx(0.4333, abs=1e-4)

    # Chrome's hours split across five activities, and no category comes close to its total.
    assert max(BY_CATEGORY.values()) < naive["chrome.exe"]


def test_both_views_account_for_the_same_seconds(loaded):
    body = window(loaded, "/summary")
    assert sum(g["duration_s"] for g in body["groups"]) == TRACKED_S
    assert sum(g["duration_s"] for g in body["naive_by_process"]) == TRACKED_S


def test_the_voice_logged_gym_block_is_a_separate_device(loaded):
    """Off-screen time, on another machine, with no process to attribute it to."""
    sessions = window(loaded, "/sessions", device_id="phone-android-01")["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["category"] == "gym"
    assert sessions[0]["duration_s"] == 3300.0
    assert sessions[0]["confidence"] == pytest.approx(0.97)


def test_the_morning_work_block_spans_two_processes_as_one_session(loaded):
    """Ninety minutes of work that an app-name tracker reports as 55 of one and 35 of another."""
    sessions = window(loaded, "/sessions", category="deep work")["sessions"]
    morning = sessions[0]

    assert morning["start_ts"] == "2026-10-28T07:45:00Z"
    assert morning["end_ts"] == "2026-10-28T09:15:00Z"
    assert morning["event_count"] == 90
    assert morning["duration_s"] == 5400.0
    assert morning["label"] == "auth refactor"


def test_the_brief_doubt_inside_the_work_block_does_not_sink_its_confidence(loaded):
    """Two doubtful minutes out of ninety move the session's confidence by two points.

    By sample count they would move it the same amount here — the samples are evenly spaced —
    but the session's confidence stays well clear of the 0.52 those two samples reported,
    which is the property the dashboard's confidence badge depends on.
    """
    morning = window(loaded, "/sessions", category="deep work")["sessions"][0]
    assert morning["confidence"] == pytest.approx(0.91, abs=0.005)


def test_the_day_keeps_two_uncertain_spans_newest_first(loaded):
    spans = loaded.get("/uncertain").json()["spans"]

    assert len(spans) == 2
    assert [s["start_ts"] for s in spans] == [
        "2026-10-28T10:51:00Z",
        "2026-10-28T09:15:00Z",
    ]
    assert [s["process"] for s in spans] == ["msedge.exe", "chrome.exe"]
    assert all(s["suggestions"] for s in spans)
    assert all(s["evidence"] for s in spans)


def test_the_browser_span_is_offered_the_browsers_own_history(loaded):
    """Chrome has been doomscrolling, research and devotion today, longest first."""
    chrome_span = loaded.get("/uncertain").json()["spans"][1]
    assert chrome_span["suggestions"] == ["doomscrolling", "research", "devotion"]


def test_resolving_a_fixture_span_re_segments_the_day(loaded):
    """One answer, and the day has one fewer session and no uncertain time left in that hour."""
    before = window(loaded, "/sessions")["sessions"]
    span = loaded.get("/uncertain").json()["spans"][1]

    resolved = loaded.post(
        f"/uncertain/{span['id']}/resolve",
        json={"category": "research", "label": "reading API documentation"},
    )
    assert resolved.status_code == 200, resolved.text

    session = resolved.json()
    # The seven uncertain minutes join the 55-minute research block that follows them.
    assert session["category"] == "research"
    assert session["start_ts"] == "2026-10-28T09:15:00Z"
    assert session["duration_s"] == 3720.0
    assert session["event_count"] == 62

    after = window(loaded, "/sessions")["sessions"]
    assert len(after) == len(before) - 1

    body = window(loaded, "/summary")
    groups = {g["key"]: g["duration_s"] for g in body["groups"]}
    assert UNCERTAIN not in groups or groups[UNCERTAIN] == 840.0 - 420.0
    assert body["total_tracked_s"] == TRACKED_S


def test_re_deriving_the_whole_day_is_byte_identical(loaded):
    """Sessions are recomputed per request, so two identical requests must agree exactly."""
    first = window(loaded, "/sessions")
    second = window(loaded, "/sessions")
    assert first == second

    assert window(loaded, "/summary") == window(loaded, "/summary")


def test_a_window_narrower_than_the_day_reports_only_that_window(loaded):
    response = loaded.get(
        "/sessions",
        params={"from": "2026-10-28T10:00:00Z", "to": "2026-10-28T11:00:00Z"},
    )
    sessions = response.json()["sessions"]
    assert [s["category"] for s in sessions] == [
        "research",
        "communication",
        UNCERTAIN,
        "doomscrolling",
    ]
    for session in sessions:
        assert session["start_ts"] >= "2026-10-28T10:00:00Z"


def test_the_fixture_day_falls_inside_the_hackathon_window():
    """A sanity check on the data, not on the code: the demo day has to be a real date."""
    day = datetime(2026, 10, 28, tzinfo=timezone.utc)
    assert datetime(2026, 10, 26, tzinfo=timezone.utc) <= day
    assert day <= datetime(2026, 11, 4, tzinfo=timezone.utc)
