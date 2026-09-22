"""Tag storage and filtering.

Tags used to live in a JSON column on `trip_posts` and were matched by loading at
most `_TAG_SCAN_LIMIT` (1000) rows and intersecting sets in Python, because JSON
containment is spelled differently on SQLite and PostgreSQL. That had three
consequences this module pins down:

* results were silently truncated past the scan window, and `total` reported the
  truncated count as if it were the real one;
* the stored form was whatever the client sent, so "hiking" and "HIKING" were
  different tags;
* there was no index that could serve "which posts carry this tag".

The replacement is a `trip_post_tags` join table (one row per tag) filtered with
an indexed `EXISTS`, and the same canonical form on every write path.
"""
import sqlite3
import uuid

import pytest

BULK = 1005


def _hex(uuid_str: str) -> str:
    """SQLAlchemy's `Uuid` stores CHAR(32) hex without dashes on SQLite."""
    return uuid_str.replace("-", "")


def _make_trip(client, user, **overrides) -> dict:
    payload = {
        "title": "Tokyo cherry blossom trip",
        "description": "Looking for a companion to explore Tokyo in spring.",
        "destination_country": "Japan",
        "destination_city": "Tokyo",
        "budget_type": "MODERATE",
        "target_gender": "ANY",
        "tags": ["PHOTOGRAPHY", "FOOD"],
        "looking_for_count": 2,
    }
    payload.update(overrides)
    resp = client.post("/api/v1/trips", headers=user["headers"], json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _tag_matches(client, user, tags: str) -> dict:
    resp = client.get(f"/api/v1/trips?tags={tags}&limit=100", headers=user["headers"])
    assert resp.status_code == 200, resp.text
    return resp.json()


def _tag_rows(raw_db, trip_id: str) -> list[str]:
    return [
        row[0]
        for row in raw_db.execute(
            "SELECT tag FROM trip_post_tags WHERE trip_post_id = ? ORDER BY tag",
            (_hex(trip_id),),
        )
    ]


# --------------------------------------------------------------------------
# Canonical form and exact matching
# --------------------------------------------------------------------------

def test_tags_are_canonicalised_and_matched_exactly(client, register_user):
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, tags=["  hiking ", "HIKING", "food", ""])

    # One canonical form, not four spellings of two tags. Alphabetical because
    # the relationship orders by tag, which keeps the display stable across reads.
    assert trip["tags"] == ["FOOD", "HIKING"]

    # Matching is case-insensitive from the caller's side...
    assert trip["id"] in {t["id"] for t in _tag_matches(client, alice, "hiking")["items"]}
    assert trip["id"] in {t["id"] for t in _tag_matches(client, alice, "HiKiNg")["items"]}

    # ...but whole-tag, not a prefix or substring. A `LIKE '%HIK%'` implementation
    # would wrongly match here; the join table cannot.
    assert _tag_matches(client, alice, "HIK")["total"] == 0
    assert _tag_matches(client, alice, "HIKINGX")["total"] == 0

    # Multiple tags are OR-ed, matching the previous in-memory semantics.
    assert trip["id"] in {
        t["id"] for t in _tag_matches(client, alice, "NOPE,HIKING")["items"]
    }

    # Whitespace around the comma is tolerated.
    assert trip["id"] in {t["id"] for t in _tag_matches(client, alice, " NOPE , FOOD ")["items"]}


def test_tag_filter_still_honours_the_other_filters(client, register_user):
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, tags=["HIKING"], destination_country="Japan")

    both = client.get(
        "/api/v1/trips?tags=HIKING&country=Japan", headers=alice["headers"]
    ).json()
    assert trip["id"] in {t["id"] for t in both["items"]}

    # The tag matches, the country does not — the tag filter must not widen the
    # result set by being applied separately from the rest of the query.
    mismatch = client.get(
        "/api/v1/trips?tags=HIKING&country=Atlantis", headers=alice["headers"]
    ).json()
    assert mismatch["total"] == 0


# --------------------------------------------------------------------------
# The scan-window regression
# --------------------------------------------------------------------------

@pytest.fixture()
def bulk_tagged_trips(client, register_user, raw_db):
    """`BULK` posts carrying one shared tag, inserted directly.

    Inserted with SQL rather than through the API because 1005 posts would
    dominate the suite's runtime, and the behaviour under test is the *read* path.
    `BULK` is deliberately just past the old 1000-row scan cap.
    """
    alice = register_user(nickname="Alice")

    owner = raw_db.execute(
        "SELECT id FROM profiles WHERE id = ?", (_hex(alice["profile"]["id"]),)
    ).fetchone()
    assert owner, "profile id is not stored in the expected CHAR(32) form"
    owner_id = owner[0]

    created = [uuid.uuid4().hex for _ in range(BULK)]
    stamp = "2026-01-01 00:00:00.000000"
    raw_db.executemany(
        "INSERT INTO trip_posts (id, creator_id, title, description, destination_country,"
        " budget_type, target_gender, looking_for_count, status, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (post_id, owner_id, f"Bulk trip {n}", "bulk trip description", "Japan",
             "MODERATE", "ANY", 1, "OPEN", stamp, stamp)
            for n, post_id in enumerate(created)
        ],
    )
    raw_db.executemany(
        "INSERT INTO trip_post_tags (trip_post_id, tag) VALUES (?, ?)",
        [(post_id, "BULK") for post_id in created],
    )
    raw_db.commit()

    try:
        yield alice
    finally:
        # Cascades to trip_post_tags (foreign keys are on), which also keeps this
        # fixture from leaking into whatever test runs next.
        raw_db.executemany("DELETE FROM trip_posts WHERE id = ?", [(i,) for i in created])
        raw_db.commit()


def test_tag_total_is_not_capped_by_a_scan_window(client, bulk_tagged_trips):
    """`total` must be the real number of matches, not the size of a scan window.

    The old filter loaded `limit(1000)` rows and reported `len(matched)` — so with
    1005 matches it answered 1000, and the five outside the window were invisible
    with nothing in the response hinting at it.
    """
    body = _tag_matches(client, bulk_tagged_trips, "BULK")

    assert body["total"] == BULK, (
        f"expected {BULK} matches, got {body['total']} — tag filtering looks "
        "truncated again (the old implementation capped its scan at 1000 rows)"
    )

    # Pagination is driven by the same query, so the last page is reachable.
    last_page = client.get(
        f"/api/v1/trips?tags=BULK&page={BULK}&limit=1", headers=bulk_tagged_trips["headers"]
    ).json()
    assert last_page["total"] == BULK
    assert len(last_page["items"]) == 1

    beyond = client.get(
        f"/api/v1/trips?tags=BULK&page={BULK + 1}&limit=1", headers=bulk_tagged_trips["headers"]
    ).json()
    assert beyond["items"] == []


# --------------------------------------------------------------------------
# Write paths: create, update, delete
# --------------------------------------------------------------------------

def test_updating_tags_replaces_and_prunes_the_rows(client, register_user, raw_db):
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, tags=["PHOTOGRAPHY", "FOOD"])
    assert _tag_rows(raw_db, trip["id"]) == ["FOOD", "PHOTOGRAPHY"]

    resp = client.patch(
        f"/api/v1/trips/{trip['id']}", headers=alice["headers"], json={"tags": ["HIKING"]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tags"] == ["HIKING"]

    assert _tag_rows(raw_db, trip["id"]) == ["HIKING"], "the removed tags left rows behind"

    # And the dropped tag no longer matches this trip. (Other tests' posts carry
    # PHOTOGRAPHY too, so assert on this trip specifically.)
    assert trip["id"] not in {t["id"] for t in _tag_matches(client, alice, "PHOTOGRAPHY")["items"]}


def test_omitting_tags_on_update_leaves_them_alone(client, register_user, raw_db):
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, tags=["HIKING", "FOOD"])

    resp = client.patch(
        f"/api/v1/trips/{trip['id']}", headers=alice["headers"], json={"title": "Renamed trip"}
    )
    assert resp.status_code == 200, resp.text
    # `exclude_unset` means an absent field is not an instruction to clear it.
    assert resp.json()["tags"] == ["FOOD", "HIKING"]
    assert _tag_rows(raw_db, trip["id"]) == ["FOOD", "HIKING"]


def test_deleting_a_trip_removes_its_tag_rows(client, register_user, raw_db):
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, tags=["HIKING"])
    assert _tag_rows(raw_db, trip["id"]) == ["HIKING"]

    resp = client.delete(f"/api/v1/trips/{trip['id']}", headers=alice["headers"])
    assert resp.status_code == 204
    assert _tag_rows(raw_db, trip["id"]) == [], "tag rows outlived their trip"


# --------------------------------------------------------------------------
# SQLite foreign-key enforcement
# --------------------------------------------------------------------------

def test_deleting_a_trip_cascades_to_applications(client, register_user, raw_db):
    """SQLite ignores `ON DELETE CASCADE` unless the connection asks for it.

    This is the sharpest available probe of `PRAGMA foreign_keys=ON`: the
    `applications` relationship is `lazy="raise"` **and** `passive_deletes=True`,
    so the ORM deliberately never loads the children and therefore never deletes
    them itself. Nothing but the database cascade can remove the row — and with
    the pragma off (SQLite's default) it silently survives, while the identical
    operation on PostgreSQL removes it.
    """
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    trip = _make_trip(client, alice)

    applied = client.post(
        f"/api/v1/trips/{trip['id']}/apply", headers=bob["headers"], json={"message": "hi"}
    )
    assert applied.status_code == 201, applied.text

    trip_id = _hex(trip["id"])
    def count_applications() -> int:
        return raw_db.execute(
            "SELECT count(*) FROM trip_applications WHERE trip_post_id = ?", (trip_id,)
        ).fetchone()[0]

    assert count_applications() == 1

    assert client.delete(f"/api/v1/trips/{trip['id']}", headers=alice["headers"]).status_code == 204

    assert count_applications() == 0, (
        "the application survived its trip — SQLite foreign keys are off, so every "
        "ondelete= rule in the models is decorative on this dialect"
    )


def test_sqlite_rejects_an_orphan_tag_row(raw_db):
    """Confirms the constraint is real, not just declared."""
    with pytest.raises(sqlite3.IntegrityError):
        raw_db.execute(
            "INSERT INTO trip_post_tags (trip_post_id, tag) VALUES (?, ?)",
            ("f" * 32, "ORPHAN"),
        )
    raw_db.rollback()
