"""A balanced headline must not conceal a duplicated or missing paired trial."""

import csv
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_checks", ROOT / "scripts/check_evidence.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


@pytest.mark.parametrize("damage", ["duplicate", "unobserved_method", "call_count"])
def test_corrupted_release_observations_cannot_pass(tmp_path, monkeypatch, damage):
    (tmp_path / "data").mkdir()
    (tmp_path / "reports").symlink_to(ROOT / "reports", target_is_directory=True)
    with (ROOT / "data/observations.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    target = next(r for r in rows if r["batch"] == "corrected-regression-v2")
    if damage == "duplicate":
        rows.append(target.copy())
    elif damage == "unobserved_method":
        target["method"] = "multi"
    else:
        target["calls"] = str(int(target["calls"]) + 1)
    with (tmp_path / "data/observations.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    monkeypatch.setattr(checks, "ROOT", tmp_path)
    with pytest.raises(ValueError):
        checks.earlier()


def test_replacing_a_whole_case_does_not_make_a_valid_holdout(monkeypatch):
    original = checks.read

    def substitute(path):
        data = original(path)
        if path.name == "fair-v2-final-independent-audit.json":
            case = data["trials"][0]["task"]
            for row in data["trials"]:
                if row["task"] == case:
                    row["task"] = "unsealed-case"
        return data

    monkeypatch.setattr(checks, "read", substitute)
    with pytest.raises(ValueError, match="Unbalanced holdout"):
        checks.fair()


def test_inflating_an_aggregate_only_panel_is_not_more_observations(monkeypatch):
    original = checks.read

    def inflated(path):
        data = original(path)
        if path.parent.name == "aidless" and path.name == "results.json":
            data["live_arms"]["bare"]["cases"] *= 10
        return data

    monkeypatch.setattr(checks, "read", inflated)
    with pytest.raises(ValueError, match="Aidless"):
        checks.fieldwork()
