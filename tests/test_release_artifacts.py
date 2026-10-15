"""A partial two-package release must never reach the PyPI upload steps."""

import io
from pathlib import Path
import runpy
import tarfile
import zipfile

import pytest

check = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/check_release_artifacts.py"))["check"]


def artifact_pair(directory, name, metadata_version="1.2.3"):
    metadata = f"Name: {name.replace('_', '-')}\nVersion: {metadata_version}\n".encode()
    with zipfile.ZipFile(directory / f"{name}-1.2.3-py3-none-any.whl", "w") as archive:
        archive.writestr(f"{name}-1.2.3.dist-info/METADATA", metadata)
    with tarfile.open(directory / f"{name}-1.2.3.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo(f"{name}-1.2.3/PKG-INFO")
        info.size = len(metadata)
        archive.addfile(info, io.BytesIO(metadata))


def test_complete_release(tmp_path):
    for name in ("altk_evolve_core", "altk_evolve"):
        artifact_pair(tmp_path, name)
    check(tmp_path, "1.2.3")


def test_single_distribution_is_not_a_complete_release(tmp_path):
    artifact_pair(tmp_path, "altk_evolve_core")
    with pytest.raises(FileNotFoundError):
        check(tmp_path, "1.2.3")


def test_metadata_must_match_release_version(tmp_path):
    artifact_pair(tmp_path, "altk_evolve_core", metadata_version="1.2.2")
    with pytest.raises(ValueError, match="metadata mismatch"):
        check(tmp_path, "1.2.3")


def test_truncated_wheel_is_not_usable(tmp_path):
    artifact_pair(tmp_path, "altk_evolve_core")
    wheel = tmp_path / "altk_evolve_core-1.2.3-py3-none-any.whl"
    wheel.write_bytes(wheel.read_bytes()[:20])
    with pytest.raises(zipfile.BadZipFile):
        check(tmp_path, "1.2.3")
