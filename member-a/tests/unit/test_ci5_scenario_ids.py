"""CI-5: derived scenario ids contain exactly one ``__d<k>_<hex6>`` segment (CONTRACT 4.2 / 5.12 root-id rule)."""
import pytest

from jp_a.domain.entities import is_valid_scenario_id
from jp_a.domain.disruptions import derived_scenario_id, root_scenario_id


@pytest.mark.parametrize(
    "sid,ok",
    [
        ("scn_sa01_w06__d1_3f9a2c", True),
        ("scn_sa01_w06_severe__d2_abcdef", True),
        ("scn_a__b__d1_abcdef", False),  # second "__"
        ("scn_a__d1_abcdef__d2_abcdef", False),  # chained suffix
        ("scn_a___d1_abcdef", False),  # root ends in "_"
        ("scn_a__d1_abcde", False),
        ("scn_a__dx_abcdef", False),
        ("scn_a__d1_abcdef_", False),
    ],
)
def test_derived_id_has_exactly_one_suffix(sid, ok):
    assert is_valid_scenario_id(sid) is ok


def test_root_rule_roundtrip():
    sid = derived_scenario_id("scn_sa01_w06", [{"type": "supply_cut", "params": {"fraction": 0.3}}])
    assert is_valid_scenario_id(sid) and root_scenario_id(sid) == "scn_sa01_w06"
