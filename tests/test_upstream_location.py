import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/upstreams.py"


def test_relocated_lab_uses_existing_workspace_references(tmp_path):
    lab = tmp_path / "runtime/loop-mechanisms"
    scripts = lab / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPT, scripts / "upstreams.py")
    references = tmp_path / "refs/upstream"
    references.mkdir(parents=True)
    env = {key: value for key, value in os.environ.items() if key != "LOOP_LAB_REFERENCES"}

    result = subprocess.run(
        [sys.executable, str(scripts / "upstreams.py"), "--show-cache"],
        env=env, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == references
    assert not (lab.parent / "references").exists()


def test_explicit_reference_location_is_used_without_creating_it(tmp_path):
    references = tmp_path / "external-cache"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--show-cache"],
        env={**os.environ, "LOOP_LAB_REFERENCES": str(references)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == references
    assert not references.exists()


def test_standalone_checkout_keeps_sibling_reference_location(tmp_path):
    scripts = tmp_path / "standalone/scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPT, scripts / "upstreams.py")
    env = {key: value for key, value in os.environ.items() if key != "LOOP_LAB_REFERENCES"}
    result = subprocess.run(
        [sys.executable, str(scripts / "upstreams.py"), "--show-cache"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == tmp_path / "references"
    assert not (tmp_path / "references").exists()


def test_missing_workspace_cache_does_not_fall_back_to_a_second_cache(tmp_path):
    lab = tmp_path / "runtime/loop-mechanisms"
    scripts = lab / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPT, scripts / "upstreams.py")
    (tmp_path / "refs").mkdir()
    env = {key: value for key, value in os.environ.items() if key != "LOOP_LAB_REFERENCES"}
    result = subprocess.run(
        [sys.executable, str(scripts / "upstreams.py"), "--show-cache"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == tmp_path / "refs/upstream"
    assert not (lab.parent / "references").exists()


def test_documented_python3_command_can_query_cache_without_project_names(tmp_path):
    result = subprocess.run(
        [shutil.which("python3") or sys.executable, str(SCRIPT), "--show-cache"],
        env={**os.environ, "LOOP_LAB_REFERENCES": str(tmp_path)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == tmp_path


def test_unknown_project_is_rejected_before_cache_access(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "unknown-project", "--cache", str(tmp_path / "cache")],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert not (tmp_path / "cache").exists()
