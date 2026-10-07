from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from harness.config import load_settings
from harness.sandbox import DockerSandbox, check_docker
from harness.verify import run_checks
from harness.workspace import prepare_workspace


pytestmark = pytest.mark.docker
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def settings():
    loaded = load_settings(PROJECT_ROOT / "harness.example.toml")
    error = check_docker(loaded.sandbox.image)
    if error is not None:
        pytest.skip(error)
    return loaded


def test_reference_fix_turns_baseline_acceptance_failure_into_passes(settings, tmp_path):
    run_id = f"acceptance-{uuid4().hex}"
    repo = prepare_workspace(
        settings.target,
        tmp_path / "cache",
        tmp_path / "workspace",
    )
    sandbox = DockerSandbox(
        settings.sandbox,
        repo,
        settings.target.acceptance_dir,
        run_id,
    )

    baseline = run_checks(sandbox, settings.checks)
    baseline_by_name = {result.name: result for result in baseline}
    assert baseline_by_name["regression"].status == "passed", baseline_by_name["regression"].output
    assert baseline_by_name["acceptance"].status == "failed", baseline_by_name["acceptance"].output
    assert "InvalidBatchRef" in baseline_by_name["acceptance"].output

    patch = PROJECT_ROOT / "acceptance" / "cosmic" / "reference_fix.patch"
    subprocess.run(
        ["git", "-C", str(repo), "apply", str(patch)],
        check=True,
        capture_output=True,
        text=True,
    )

    after_fix = run_checks(sandbox, settings.checks)
    after_fix_by_name = {result.name: result for result in after_fix}
    assert after_fix_by_name["regression"].status == "passed", after_fix_by_name["regression"].output
    assert after_fix_by_name["acceptance"].status == "passed", after_fix_by_name["acceptance"].output
