"""Build the wheel, install it into a clean venv, and run the self-check from there.

Guards against package-data omissions: a wheel without default_policy.json fails closed and blocks everything.
"""

import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.environ.get("GITVOW_SKIP_PACKAGING") == "1", reason="packaging test disabled")
def test_wheel_contains_policy_and_selftest_passes(tmp_path):
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "-q", "--no-deps", "-w", str(tmp_path), str(ROOT)], check=True
    )
    wheel = next(tmp_path.glob("gitvow-*.whl"))
    import zipfile

    assert "gitvow/default_policy.json" in zipfile.ZipFile(wheel).namelist()
    env_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(env_dir)
    bin_dir = env_dir / ("Scripts" if os.name == "nt" else "bin")
    subprocess.run([str(bin_dir / "python"), "-m", "pip", "install", "-q", str(wheel)], check=True)
    r = subprocess.run([str(bin_dir / "gitvow"), "selftest"], capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 failed" in r.stdout
    import re

    expected = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    v = subprocess.run([str(bin_dir / "gitvow"), "--version"], capture_output=True, text=True, check=True)
    assert v.stdout.strip() == f"gitvow {expected}"
