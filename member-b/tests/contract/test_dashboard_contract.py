from pathlib import Path
import re
import pytest

DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "dashboard"
SRC_DIR = DASHBOARD_DIR / "src"


def test_vite_config_conformance():
    """
    CONTRACT §9.2:
    - dedupe: react, react-dom, react/jsx-runtime
    - fs.allow: '.', '../../member-a/dashboard'
    - port: 5173
    - alias @jp/a-screens
    """
    vite_config_path = DASHBOARD_DIR / "vite.config.ts"
    assert vite_config_path.exists(), "vite.config.ts must exist"
    content = vite_config_path.read_text(encoding="utf-8")

    assert "@jp/a-screens" in content
    assert "aScreens.tsx" in content
    assert "react/jsx-runtime" in content
    assert "../../member-a/dashboard" in content
    assert "5173" in content
    assert "8002" in content
    assert "8001" in content


def test_mock_a_screens_exports():
    """
    CONTRACT §9.2 & MEMBER_B.md §5.2:
    aScreens.tsx must export SnapshotScreen, DispatchPlanScreen, and A_SCREENS_CONTRACT_VERSION = '1.0.0'.
    """
    mock_file = SRC_DIR / "mocks" / "aScreens.tsx"
    assert mock_file.exists(), "aScreens.tsx must exist"
    content = mock_file.read_text(encoding="utf-8")

    assert "export const A_SCREENS_CONTRACT_VERSION = \"1.0.0\";" in content
    assert "export const SnapshotScreen" in content
    assert "export const DispatchPlanScreen" in content


def test_isolation_lint_no_direct_a_api_calls():
    """
    MEMBER_B.md §6 Item 7:
    Isolation lint: a grep test asserts that dashboard/src/**, excluding the alias config,
    contains no '/a/v1' string literal except the single aApiBase = '/a/v1' constant passed as a prop.
    """
    ts_files = list(SRC_DIR.glob("**/*.ts")) + list(SRC_DIR.glob("**/*.tsx"))
    matches = []

    for file_path in ts_files:
        text = file_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for idx, line in enumerate(lines, 1):
            code_part = line.split("//")[0].strip()
            # Check for "/a/v1" literal in code (excluding comments)
            if '"/a/v1"' in code_part or "'/a/v1'" in code_part:
                matches.append((file_path.name, idx, line.strip()))

    # There must be exactly 1 code occurrence where aApiBase = "/a/v1" is defined/passed
    assert len(matches) == 1, f"Expected 1 code match for /a/v1, found: {matches}"
    file_name, line_no, line_content = matches[0]
    assert "A_API_BASE" in line_content or "aApiBase" in line_content
    assert "/a/v1" in line_content


def test_tokens_css_conformance():
    """
    CONTRACT §9.6: Design tokens on :root and body background --jp-bg.
    """
    tokens_file = SRC_DIR / "styles" / "tokens.css"
    assert tokens_file.exists(), "tokens.css must exist"
    content = tokens_file.read_text(encoding="utf-8")

    required_tokens = [
        "--jp-bg",
        "--jp-surface",
        "--jp-text",
        "--jp-muted",
        "--jp-border",
        "--jp-accent",
        "--jp-accent-weak",
        "--jp-danger",
        "--jp-danger-weak",
        "--jp-warn",
        "--jp-ok",
        "--jp-strat-jalpriority",
        "--jp-strat-fcfs",
        "--jp-strat-nearest",
        "--jp-font",
        "--jp-mono",
        "--jp-radius",
        "--jp-space",
    ]

    for token in required_tokens:
        assert token in content, f"Missing design token {token} in tokens.css"

    assert "var(--jp-bg)" in content, "Body must use --jp-bg"
