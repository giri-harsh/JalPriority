"""Generator determinism, committed artefacts, CLI and script (MEMBER_A F1)."""
from __future__ import annotations

import pytest

from jp_a.data import cli
from jp_a.data.generator import DEFAULT_SEED, SEED_VARIANTS, generate_files, write_files
from jp_a.domain.entities import Scenario

from .data_helpers import DATA_DIR, MEMBER_A_DIR, SEED_IDS

EXPECTED_FILES = {
    *(f"scenarios/{sid}.json" for sid in SEED_IDS),
    *(f"truth/{sid}.json" for sid in SEED_IDS),
    "labels.json",
    "training/train.csv",
    "training/test.csv",
}


@pytest.fixture(scope="module")
def default_files() -> dict[str, str]:
    return generate_files(DEFAULT_SEED)


def test_generates_exactly_the_expected_files(default_files):
    assert set(default_files) == EXPECTED_FILES


def test_two_runs_write_byte_identical_files(tmp_path):
    first = write_files(tmp_path / "run1", DEFAULT_SEED)
    second = write_files(tmp_path / "run2", DEFAULT_SEED)
    assert set(first) == set(second) == EXPECTED_FILES
    for relpath in first:
        assert first[relpath].read_bytes() == second[relpath].read_bytes(), relpath


def test_committed_data_matches_default_seed(default_files):
    """Regenerating with the default seed reproduces the committed files exactly."""
    for relpath, text in default_files.items():
        committed = (DATA_DIR / relpath).read_bytes()
        assert committed == text.encode("utf-8"), f"{relpath} is stale: run scripts/generate_data.sh"


def test_committed_files_are_utf8_with_lf_newlines():
    for relpath in EXPECTED_FILES:
        raw = (DATA_DIR / relpath).read_bytes()
        assert b"\r" not in raw, relpath
        assert raw.endswith(b"\n"), relpath
        raw.decode("utf-8")


def test_committed_data_is_small():
    total = sum(p.stat().st_size for p in DATA_DIR.rglob("*") if p.is_file())
    assert total < 4 * 1024 * 1024


def test_seed_changes_the_data_but_stays_contract_valid(default_files):
    other = generate_files(7)
    assert other["training/train.csv"] != default_files["training/train.csv"]
    assert other["scenarios/scn_sa01_w06.json"] != default_files["scenarios/scn_sa01_w06.json"]
    for sid in SEED_IDS:
        scenario = Scenario.model_validate_json(other[f"scenarios/{sid}.json"])
        assert scenario.seed == 7 and len(scenario.areas) == 8


def test_negative_seed_is_rejected():
    with pytest.raises(ValueError):
        generate_files(-1)


def test_variant_table_matches_the_spec():
    assert [(v.scenario_id, v.alert_level, v.supply_share) for v in SEED_VARIANTS] == [
        ("scn_sa01_w06", 2, 0.55),
        ("scn_sa01_w06_severe", 3, 0.35),
        ("scn_sa01_w06_mild", 1, 0.85),
    ]


def test_cli_writes_the_data_set(tmp_path, capsys, default_files):
    assert cli.main(["--out", str(tmp_path), "--seed", str(DEFAULT_SEED)]) == 0
    for relpath, text in default_files.items():
        assert (tmp_path / relpath).read_bytes() == text.encode("utf-8")
    assert "seed 42" in capsys.readouterr().out


def test_cli_rejects_a_negative_seed(tmp_path, capsys):
    assert cli.main(["--out", str(tmp_path), "--seed", "-3"]) == 2
    assert not any(tmp_path.iterdir())
    assert "non-negative" in capsys.readouterr().err


def test_cli_requires_out():
    with pytest.raises(SystemExit):
        cli.main([])


def test_generate_data_script_runs_the_cli_into_member_a_data():
    script = MEMBER_A_DIR / "scripts" / "generate_data.sh"
    raw = script.read_bytes()
    assert raw.startswith(b"#!/usr/bin/env bash\n") and b"\r" not in raw
    text = raw.decode("utf-8")
    assert "-m jp_a.data.cli --out data" in text
    assert ".venv/Scripts/python.exe" in text and ".venv/bin/python" in text
