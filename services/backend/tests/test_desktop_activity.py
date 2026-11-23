import json

import pytest

pytest.importorskip("mydl_desktop")

from mydl_desktop.activity import MAX_ENTRIES, ActivityStore


def test_record_persists_and_reloads(tmp_path):
    path = tmp_path / "state" / "activity.json"
    store = ActivityStore(path)
    entry = store.record({"id": "job1", "title": "First video", "status": "done"})

    assert entry["finished_at"]
    assert path.exists()

    reloaded = ActivityStore(path)
    assert reloaded.find("job1")["title"] == "First video"
    assert reloaded.latest()["id"] == "job1"


def test_list_returns_newest_first_and_respects_the_limit(tmp_path):
    store = ActivityStore(tmp_path / "activity.json")
    for index in range(5):
        store.record({"id": f"job{index}", "status": "done"})

    assert [entry["id"] for entry in store.list(limit=2)] == ["job4", "job3"]
    assert store.list(limit=0)[0]["id"] == "job4"


def test_recording_the_same_id_replaces_the_old_entry(tmp_path):
    store = ActivityStore(tmp_path / "activity.json")
    store.record({"id": "job1", "title": "Old", "status": "failed"})
    store.record({"id": "job1", "title": "New", "status": "done"})

    entries = store.list(limit=10)
    assert len(entries) == 1
    assert entries[0]["title"] == "New"


def test_store_trims_entries_to_the_limit(tmp_path):
    store = ActivityStore(tmp_path / "activity.json", limit=3)
    for index in range(6):
        store.record({"id": f"job{index}", "status": "done"})

    assert [entry["id"] for entry in store.list(limit=10)] == ["job5", "job4", "job3"]
    assert len(json.loads((tmp_path / "activity.json").read_text(encoding="utf-8"))) == 3
    assert MAX_ENTRIES >= 3


def test_store_recovers_from_a_corrupt_file(tmp_path):
    path = tmp_path / "activity.json"
    path.write_text("{not json", encoding="utf-8")
    store = ActivityStore(path)
    assert store.list() == []
    assert store.latest() is None

    path.write_text('{"not": "a list"}', encoding="utf-8")
    assert ActivityStore(path).list() == []


def test_find_returns_a_copy_and_falls_back(tmp_path):
    store = ActivityStore(tmp_path / "activity.json")
    store.record({"id": "job1", "status": "done"})

    found = store.find("job1")
    found["status"] = "changed"
    assert store.find("job1")["status"] == "done"
    assert store.find("unknown") is None
