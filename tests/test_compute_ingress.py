from app.capabilities.analytics.tool import canonicalize_metrics
from app.runtime.engine import validate_ingress, IngressError
import pytest


def test_canonicalize_fixes_inconsistent_total_bases():
    rows = [
        {
            "singles": 73,
            "doubles": 26,
            "triples": 8,
            "home_runs": 44,
            "hits": 151,
            "at_bats": 495,
            "walks": 67,
            "hbp": 3,
            "sac_flies": 3,
            "pa": 573,
            "strikeouts": 142,
            "tb": 177,  # deliberately wrong
            "slg": 0.358,
            "avg": 0.305,
            "obp": 0.389,
            "ops": 0.747,
            "k_pct": 0.248,
            "bb_pct": 0.117,
        }
    ]
    fixed, normalized = canonicalize_metrics(rows)
    row = fixed[0]
    assert row["tb"] == 73 + 2 * 26 + 3 * 8 + 4 * 44
    assert round(row["slg"], 4) == round(row["tb"] / 495, 4)
    assert round(row["ops"], 4) == round(row["obp"] + row["slg"], 4)
    assert round(row["k_pct"], 1) == round(100 * 142 / 573, 1)
    assert "slg" in normalized and "tb" in normalized


def test_valid_ascii_ingress():
    text = "大谷翔平 2025 Ohtani"
    assert validate_ingress(text) == text


def test_illegal_surrogate_rejected():
    with pytest.raises(IngressError):
        validate_ingress("bad\ud800value")


def test_nul_byte_rejected():
    with pytest.raises(IngressError):
        validate_ingress("bad\x00value")


def test_empty_rejected():
    with pytest.raises(IngressError):
        validate_ingress("   ")
