"""Tests for the five endpoints of `docs/API.md`.

Driven through `TestClient` against a real store on disk, so what is tested is the
serialisation a dashboard will actually receive — a test that asserted on the handler's
return value would pass happily while the JSON said something else.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import T0, run
from fastapi.testclient import TestClient

from groundtruth_server.app import create_app
from groundtruth_server.config import Settings
from groundtruth_server.models import UNCERTAIN
from groundtruth_server.timeutil import to_iso

DAY_FROM = "2026-10-28T00:00:00Z"
DAY_TO = "2026-10-28T23:59:59Z"


@pytest.fixture
def client(store) -> TestClient:
    with TestClient(create_app(store=store, settings=Settings(db_path=store.path))) as c:
        yield c


def wire(events) -> dict:
    """Render domain events as a `POST /events` body."""
    return {
        "device_id": events[0].device_id,
        "events": [
            {
                "ts": to_iso(event.ts),
                "tier": int(event.tier),
                "process": event.process,
                "window_title": event.window_title,
                "label": event.label,
                "category": event.category,
                "confidence": event.confidence,
                "evidence": event.classification.evidence,
            }
            for event in events
        ],
    }


def seed(client: TestClient, events) -> dict:
    response = client.post("/events", json=wire(events))
    assert response.status_code == 202, response.text
    return response.json()


def day(client: TestClient, path: str, **params) -> dict:
    response = client.get(path, params={"from": DAY_FROM, "to": DAY_TO, **params})
    assert response.status_code == 200, response.text
    return response.json()


# ----------------------------------------------------------------- POST /events


def test_posting_events_is_accepted(client):
    assert seed(client, run(range(0, 301, 60), "research")) == {
        "accepted": 6,
        "duplicates": 0,
    }


def test_a_retried_batch_is_reported_not_rejected(client):
    batch = run(range(0, 301, 60), "research")
    seed(client, batch)
    assert seed(client, batch) == {"accepted": 0, "duplicates": 6}


def test_a_naive_timestamp_is_rejected_at_the_boundary(client):
    body = wire(run(range(0, 61, 60), "research"))
    body["events"][0]["ts"] = "2026-10-28T09:00:00"
    response = client.post("/events", json=body)
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_an_out_of_range_confidence_is_rejected(client):
    body = wire(run(range(0, 61, 60), "research"))
    body["events"][0]["confidence"] = 1.4
    assert client.post("/events", json=body).status_code == 422


def test_an_unknown_tier_is_rejected(client):
    body = wire(run(range(0, 61, 60), "research"))
    body["events"][0]["tier"] = 4
    assert client.post("/events", json=body).status_code == 422


def test_an_event_carrying_its_own_device_id_is_rejected(client):
    """A device writes its own stream; a per-event id could only disagree with the envelope."""
    body = wire(run(range(0, 61, 60), "research"))
    body["events"][0]["device_id"] = "someone-else"
    assert client.post("/events", json=body).status_code == 422


def test_no_endpoint_accepts_an_image(client):
    """The privacy model as a test: screen content never reaches this server.

    Asserted against the generated schema rather than by attempting an upload, so adding
    such an endpoint fails the suite instead of merely being reachable. Every request body
    the server declares has to be JSON — a binary or multipart body is the shape a frame
    would have to arrive in.
    """
    schema = client.get("/openapi.json").json()
    bodies = [
        (path, method, set(operation["requestBody"]["content"]))
        for path, methods in schema["paths"].items()
        for method, operation in methods.items()
        if "requestBody" in operation
    ]
    assert bodies, "expected at least one endpoint to accept a body"
    for path, method, content_types in bodies:
        assert content_types == {"application/json"}, f"{method.upper()} {path}: {content_types}"

    schema_blob = repr(schema.get("components", {})).lower()
    for term in ("binary", "base64", "image", "png"):
        assert term not in schema_blob, f"a schema component mentions {term!r}"


# ---------------------------------------------------------------- GET /sessions


def test_sessions_match_the_contract_shape(client):
    seed(client, run(range(0, 601, 60), "research", label="reading API documentation"))
    sessions = day(client, "/sessions")["sessions"]

    assert len(sessions) == 1
    assert set(sessions[0]) == {
        "id",
        "start_ts",
        "end_ts",
        "device_id",
        "category",
        "label",
        "confidence",
        "event_count",
        "duration_s",
        "evidence_summary",
    }
    assert sessions[0]["id"].startswith("s_")
    assert sessions[0]["start_ts"] == "2026-10-28T09:00:00Z"
    assert sessions[0]["category"] == "research"
    assert sessions[0]["label"] == "reading API documentation"
    assert sessions[0]["duration_s"] == 660.0


def test_a_window_is_required(client):
    """Both bounds are listed in the contract without being marked optional."""
    assert client.get("/sessions").status_code == 422
    assert client.get("/sessions", params={"from": DAY_FROM}).status_code == 422


def test_a_backwards_window_is_refused(client):
    response = client.get("/sessions", params={"from": DAY_TO, "to": DAY_FROM})
    assert response.status_code == 422
    assert response.json()["status"] == 422


def test_an_empty_window_returns_an_empty_list(client):
    seed(client, run(range(0, 301, 60), "research"))
    response = client.get(
        "/sessions", params={"from": "2026-10-29T00:00:00Z", "to": "2026-10-29T23:59:59Z"}
    )
    assert response.json() == {"sessions": []}


def test_sessions_can_be_filtered_by_device(client):
    seed(client, run(range(0, 121, 60), "research", device="desk-win-01"))
    seed(client, run(range(300, 421, 60), "research", device="phone-android-01"))

    everyone = day(client, "/sessions")["sessions"]
    desk = day(client, "/sessions", device_id="desk-win-01")["sessions"]

    assert len(everyone) == 2
    assert [s["device_id"] for s in desk] == ["desk-win-01"]


def test_filtering_by_device_does_not_splice_the_other_devices_sessions_together(client):
    """Filtering after derivation, not before, is a correctness rule rather than a style.

    Dropping the phone's events first would leave two desktop blocks 60s apart, which would
    merge into one session reporting a continuous stretch that the handoff interrupted.
    """
    seed(
        client,
        run(range(0, 61, 60), "deep work", device="desk-win-01")
        + run(range(180, 241, 60), "deep work", device="desk-win-01"),
    )
    seed(client, run(range(120, 121, 60), "communication", device="phone-android-01"))

    desk = day(client, "/sessions", device_id="desk-win-01")["sessions"]
    assert len(desk) == 2


def test_sessions_can_be_filtered_by_category(client):
    seed(client, run(range(0, 121, 60), "research") + run(range(180, 301, 60), "deep work"))
    only = day(client, "/sessions", category="deep work")["sessions"]
    assert [s["category"] for s in only] == ["deep work"]


# ----------------------------------------------------------------- GET /summary


def test_summary_matches_the_contract_shape(client):
    seed(client, run(range(0, 601, 60), "research"))
    body = day(client, "/summary")

    assert set(body) == {"from", "to", "total_tracked_s", "groups", "naive_by_process"}
    assert body["from"] == DAY_FROM
    assert body["to"] == DAY_TO
    assert set(body["groups"][0]) == {"key", "duration_s", "share"}


def test_the_real_and_the_naive_totals_cover_the_same_seconds(client):
    """Both sides of the comparison come off one derivation, so they must reconcile."""
    seed(
        client,
        run(range(0, 601, 60), "deep work", process="code.exe")
        + run(range(660, 1261, 60), "doomscrolling", process="chrome.exe"),
    )
    body = day(client, "/summary")

    assert sum(g["duration_s"] for g in body["groups"]) == body["total_tracked_s"]
    assert sum(g["duration_s"] for g in body["naive_by_process"]) == body["total_tracked_s"]
    assert sum(g["share"] for g in body["groups"]) == pytest.approx(1.0, abs=1e-3)


def test_the_naive_view_collapses_what_the_real_view_separates(client):
    """One browser, three activities: the comparison the product exists to make."""
    seed(
        client,
        run(range(0, 301, 60), "devotion", process="chrome.exe")
        + run(range(360, 661, 60), "research", process="chrome.exe")
        + run(range(720, 1021, 60), "doomscrolling", process="chrome.exe"),
    )
    body = day(client, "/summary")

    assert [g["key"] for g in body["naive_by_process"]] == ["chrome.exe"]
    assert body["naive_by_process"][0]["share"] == pytest.approx(1.0)
    assert {g["key"] for g in body["groups"]} == {"devotion", "research", "doomscrolling"}


def test_off_screen_time_is_named_rather_than_dropped(client):
    """An app-name tracker has no row for the gym; the blind spot is shown, not hidden."""
    from groundtruth_server.models import Tier

    seed(client, run(range(0, 601, 60), "deep work", process="code.exe"))
    seed(
        client,
        run(
            range(1200, 1801, 60),
            "gym",
            device="phone-android-01",
            process=None,
            tier=Tier.VOICE,
            window_title=None,
        ),
    )
    body = day(client, "/summary")

    naive = {g["key"]: g["duration_s"] for g in body["naive_by_process"]}
    assert naive["(off-screen)"] == 660.0
    assert sum(naive.values()) == body["total_tracked_s"]


def test_summary_can_group_by_label(client):
    seed(
        client,
        run(range(0, 301, 60), "research", label="reading API documentation")
        + run(range(360, 661, 60), "research", label="comparing inference providers"),
    )
    body = day(client, "/summary", group_by="label")
    assert {g["key"] for g in body["groups"]} == {
        "reading API documentation",
        "comparing inference providers",
    }


def test_an_unknown_group_by_is_refused(client):
    response = client.get(
        "/summary", params={"from": DAY_FROM, "to": DAY_TO, "group_by": "process"}
    )
    assert response.status_code == 422


def test_summary_of_an_empty_window_is_zeroed_not_undefined(client):
    body = day(client, "/summary")
    assert body["total_tracked_s"] == 0.0
    assert body["groups"] == []
    assert body["naive_by_process"] == []


# --------------------------------------------------------------- GET /uncertain


def test_uncertain_spans_match_the_contract_shape(client):
    seed(
        client,
        run(range(0, 121, 60), "research")
        + run(range(180, 541, 60), UNCERTAIN, confidence=0.38)
        + run(range(600, 721, 60), "doomscrolling"),
    )
    spans = client.get("/uncertain").json()["spans"]

    assert len(spans) == 1
    assert set(spans[0]) == {"id", "start_ts", "end_ts", "process", "evidence", "suggestions"}
    assert spans[0]["id"].startswith("u_")
    assert spans[0]["process"] == "chrome.exe"


def test_uncertain_spans_are_newest_first(client):
    seed(
        client,
        run(range(0, 121, 60), UNCERTAIN, confidence=0.4)
        + run(range(300, 421, 60), "research")
        + run(range(600, 721, 60), UNCERTAIN, confidence=0.4),
    )
    spans = client.get("/uncertain").json()["spans"]
    assert [s["start_ts"] for s in spans] == [
        to_iso(T0 + timedelta(seconds=600)),
        to_iso(T0),
    ]


def test_suggestions_prefer_what_the_process_has_turned_out_to_be(client):
    """Statistics about this browser beat whatever happened to be on screen either side."""
    seed(
        client,
        run(range(0, 1201, 60), "doomscrolling", process="chrome.exe")
        + run(range(1800, 2401, 60), "research", process="chrome.exe")
        + run(range(3000, 3061, 60), "deep work", process="code.exe")
        + run(range(3120, 3241, 60), UNCERTAIN, process="chrome.exe", confidence=0.4),
    )
    spans = client.get("/uncertain").json()["spans"]
    assert spans[0]["suggestions"][:2] == ["doomscrolling", "research"]


def test_nothing_uncertain_is_an_empty_list(client):
    seed(client, run(range(0, 301, 60), "research"))
    assert client.get("/uncertain").json() == {"spans": []}


# -------------------------------------------- POST /uncertain/{id}/resolve


@pytest.fixture
def uncertain_day(client):
    """A day with one uncertain span between two stretches of research."""
    seed(
        client,
        run(range(0, 121, 60), "research", label="reading API documentation")
        + run(range(180, 301, 60), UNCERTAIN, confidence=0.4)
        + run(range(360, 481, 60), "research", label="reading API documentation"),
    )
    return client.get("/uncertain").json()["spans"][0]


def test_resolving_returns_the_re_segmented_session(client, uncertain_day):
    """The answer is not stored and echoed — it is re-derived, which is why it merges.

    Three sessions become one, because with the question answered that is what the day was.
    """
    response = client.post(
        f"/uncertain/{uncertain_day['id']}/resolve",
        json={"category": "research", "label": "reading API documentation"},
    )
    assert response.status_code == 200, response.text
    session = response.json()

    assert session["category"] == "research"
    assert session["event_count"] == 9
    assert session["start_ts"] == to_iso(T0)
    assert session["confidence"] > 0.9


def test_resolving_removes_the_span_from_the_question_list(client, uncertain_day):
    client.post(
        f"/uncertain/{uncertain_day['id']}/resolve",
        json={"category": "research", "label": "docs"},
    )
    assert client.get("/uncertain").json() == {"spans": []}


def test_resolving_stores_a_few_shot_example_keyed_by_process(client, store, uncertain_day):
    """This is how the taxonomy converges on the individual rather than on an average."""
    client.post(
        f"/uncertain/{uncertain_day['id']}/resolve",
        json={"category": "research", "label": "comparing inference providers"},
    )
    examples = store.few_shot_for("chrome.exe")

    assert len(examples) == 1
    assert examples[0].category == "research"
    assert examples[0].label == "comparing inference providers"
    assert examples[0].source_span_id == uncertain_day["id"]
    assert examples[0].evidence


def test_resolving_does_not_delete_the_events_underneath(client, store, uncertain_day):
    """Events are append-only. A relabel is an overlay, not an edit."""
    before = store.count_events()
    client.post(
        f"/uncertain/{uncertain_day['id']}/resolve",
        json={"category": "research", "label": "docs"},
    )
    assert store.count_events() == before
    assert any(
        event.category == UNCERTAIN for event in store.events_between(T0, T0 + timedelta(hours=1))
    )


def test_resolving_twice_is_safe(client, uncertain_day):
    """A client that retried a request whose response it never saw is not an error case."""
    first = client.post(
        f"/uncertain/{uncertain_day['id']}/resolve",
        json={"category": "research", "label": "docs"},
    )
    second = client.post(
        f"/uncertain/{uncertain_day['id']}/resolve",
        json={"category": "research", "label": "docs"},
    )
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_a_corrected_answer_supersedes_the_first(client, uncertain_day):
    client.post(
        f"/uncertain/{uncertain_day['id']}/resolve",
        json={"category": "research", "label": "docs"},
    )
    corrected = client.post(
        f"/uncertain/{uncertain_day['id']}/resolve",
        json={"category": "learning", "label": "a course"},
    )
    assert corrected.json()["category"] == "learning"


def test_resolving_an_unknown_span_is_a_problem_json_404(client):
    response = client.post(
        "/uncertain/u_00000000000000000000000000/resolve",
        json={"category": "research", "label": "docs"},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 404
    assert response.json()["title"] == "Not Found"


def test_an_empty_answer_is_refused(client, uncertain_day):
    response = client.post(
        f"/uncertain/{uncertain_day['id']}/resolve", json={"category": "", "label": ""}
    )
    assert response.status_code == 422


def test_resolving_a_session_that_is_not_uncertain_is_a_404(client):
    """Session ids and span ids are both derived; only an uncertain one is answerable."""
    seed(client, run(range(0, 301, 60), "research"))
    session_id = day(client, "/sessions")["sessions"][0]["id"]
    response = client.post(
        f"/uncertain/{session_id}/resolve", json={"category": "research", "label": "docs"}
    )
    assert response.status_code == 404


# ----------------------------------------------------------------------- misc


def test_the_contract_endpoints_are_all_served(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths) == {
        "/events",
        "/sessions",
        "/summary",
        "/uncertain",
        "/uncertain/{span_id}/resolve",
    }


def test_health_reports_the_store_size(client):
    seed(client, run(range(0, 301, 60), "research"))
    assert client.get("/health").json() == {"status": "ok", "events": 6}
