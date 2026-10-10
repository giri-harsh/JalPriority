"""M1: dashboard/src/contract/types.ts == CONTRACT section 9.4 code block (whitespace-normalised).

OWNER: LEAD (read-only for agents)."""
from __future__ import annotations

import re


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _contract_ts_block(repo_root) -> str:
    text = (repo_root / "contracts" / "CONTRACT.md").read_text(encoding="utf-8")
    m = re.search(r"### 9\.4 Props contract.*?```ts\n(.*?)```", text, flags=re.S)
    assert m, "section 9.4 ts block not found in CONTRACT.md"
    return m.group(1)


def test_types_ts_matches_contract_9_4(repo_root, member_a_dir):
    ours = (member_a_dir / "dashboard" / "src" / "contract" / "types.ts").read_text(encoding="utf-8")
    assert _norm(ours) == _norm(_contract_ts_block(repo_root))


def test_types_ts_is_byte_identical_modulo_line_endings(repo_root, member_a_dir):
    ours = (member_a_dir / "dashboard" / "src" / "contract" / "types.ts").read_bytes().replace(b"\r\n", b"\n")
    theirs = _contract_ts_block(repo_root).encode("utf-8").replace(b"\r\n", b"\n")
    assert ours == theirs
