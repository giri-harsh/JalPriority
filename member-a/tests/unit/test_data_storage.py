"""LocalStorage + make_storage."""
from __future__ import annotations

import json
import threading

import pytest

from jp_a.config import Settings
from jp_a.repositories.storage_local import LocalStorage, json_text, make_storage


@pytest.fixture()
def storage(tmp_path) -> LocalStorage:
    return LocalStorage(tmp_path)


def test_round_trip_and_canonical_text(storage, tmp_path):
    data = {"b": 1, "a": [1.5, None, True], "label": "supply ≈55% · heat alert → 3"}
    storage.write_json("scenarios/x.json", data)
    assert storage.read_json("scenarios/x.json") == data
    raw = (tmp_path / "scenarios" / "x.json").read_bytes()
    assert raw == json_text(data).encode("utf-8")
    assert raw.startswith(b'{\n  "b": 1,')  # insertion order, indent 2
    assert raw.endswith(b"}\n") and b"\r" not in raw
    assert "≈55%".encode() in raw  # UTF-8, not \u escapes


def test_write_replaces_atomically_without_leftovers(storage, tmp_path):
    storage.write_json("derived/a.json", {"v": 1})
    storage.write_json("derived/a.json", {"v": 2})
    assert storage.read_json("derived/a.json") == {"v": 2}
    assert [p.name for p in (tmp_path / "derived").iterdir()] == ["a.json"]


def test_missing_files(storage):
    assert storage.read_json("scenarios/missing.json") is None
    assert storage.exists("scenarios/missing.json") is False
    assert storage.list_json("nowhere") == []


def test_exists_and_list(storage, tmp_path):
    for name in ("b.json", "a.json"):
        storage.write_json(f"scenarios/{name}", {})
    (tmp_path / "scenarios" / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / "scenarios" / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "scenarios" / "nested").mkdir()
    storage.write_json("scenarios/nested/c.json", {})
    assert storage.exists("scenarios/a.json")
    assert not storage.exists("scenarios")  # directories are not files
    assert storage.list_json("scenarios") == ["scenarios/a.json", "scenarios/b.json"]
    assert storage.list_json("scenarios/") == ["scenarios/a.json", "scenarios/b.json"]


@pytest.mark.parametrize(
    "relpath",
    ["../x.json", "scenarios/../../x.json", "/etc/passwd", "C:/Windows/x.json", "", "..\\x.json"],
)
def test_paths_cannot_escape_the_root(storage, relpath):
    with pytest.raises(ValueError):
        storage.read_json(relpath)
    with pytest.raises(ValueError):
        storage.write_json(relpath, {})
    with pytest.raises(ValueError):
        storage.exists(relpath)


def test_backslashes_are_normalised(storage):
    storage.write_json("truth\\t.json", {"ok": True})
    assert storage.read_json("truth/t.json") == {"ok": True}


def test_rejects_nan(storage):
    with pytest.raises(ValueError):
        storage.write_json("x.json", {"v": float("nan")})


def test_concurrent_writers_leave_valid_json(storage, tmp_path):
    errors: list[BaseException] = []

    def writer(k: int) -> None:
        try:
            for i in range(10):
                storage.write_json("derived/shared.json", {"writer": k, "i": i, "pad": "x" * 2000})
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(k,)) for k in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    data = json.loads((tmp_path / "derived" / "shared.json").read_text(encoding="utf-8"))
    assert data["i"] == 9
    assert [p.name for p in (tmp_path / "derived").iterdir()] == ["shared.json"]


def test_make_storage_local(tmp_path):
    storage = make_storage(Settings(a_data_dir=tmp_path, a_storage_mode="local"))
    assert isinstance(storage, LocalStorage) and storage.root == tmp_path


def test_make_storage_s3_falls_back_to_local(tmp_path):
    storage = make_storage(Settings(a_data_dir=tmp_path, a_storage_mode="s3", a_s3_bucket="bucket"))
    assert isinstance(storage, LocalStorage) and storage.root == tmp_path
