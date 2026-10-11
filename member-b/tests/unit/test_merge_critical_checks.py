import pytest
import re
import json
import hashlib
from jp_b.clients.a_mock import AMockClient

@pytest.mark.anyio
async def test_supply_cut_decimal_precision():
    """Check 1: Verify supply_cut with S=20000 and fraction=0.9 returns exactly 2000."""
    client = AMockClient()
    scenario = await client.get_scenario("scn_sa01_w06")
    assert scenario.supply.tanker_water_available_litres == 20000

    derived = await client.apply_disruption("scn_sa01_w06", {"type": "supply_cut", "params": {"fraction": 0.9}})
    assert derived.supply.tanker_water_available_litres == 2000


@pytest.mark.anyio
async def test_derived_scenario_id_hash_and_format():
    """Check 2: Verify derived scenario ID format, single __d, SHA-1 hash, and idempotency."""
    client = AMockClient()
    disruption = {"type": "tanker_down", "params": {"tanker_id": "tk_01"}}
    derived = await client.apply_disruption("scn_sa01_w06", disruption)

    # 1. Format: ^scn_sa01_w06__d1_[0-9a-f]{6}$
    pattern = r"^scn_sa01_w06__d1_[0-9a-f]{6}$"
    assert re.match(pattern, derived.scenario_id)

    # 2. Exactly one __d
    assert derived.scenario_id.count("__d") == 1

    # 3. Canonical hash matches SHA-1 of json
    canonical = json.dumps([{"type": "tanker_down", "params": {"tanker_id": "tk_01"}}], sort_keys=True, separators=(",", ":")).encode()
    h = hashlib.sha1(canonical).hexdigest()[:6]
    assert derived.scenario_id == f"scn_sa01_w06__d1_{h}"

    # 4. Idempotency: applying same disruption returns identical derived scenario ID
    derived2 = await client.apply_disruption("scn_sa01_w06", disruption)
    assert derived2.scenario_id == derived.scenario_id
