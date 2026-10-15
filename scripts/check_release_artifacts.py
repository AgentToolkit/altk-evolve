"""Require a usable wheel and sdist for both distributions at the release version."""

import argparse
from email.parser import BytesParser
from pathlib import Path
import tarfile
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def check(directory: Path, version: str) -> None:
    for name in ("altk_evolve_core", "altk_evolve"):
        wheel = directory / f"{name}-{version}-py3-none-any.whl"
        sdist = directory / f"{name}-{version}.tar.gz"
        with zipfile.ZipFile(wheel) as archive:
            if archive.testzip() is not None:
                raise ValueError(f"Corrupt wheel: {wheel}")
            metadata = archive.read(f"{name}-{version}.dist-info/METADATA")
        _check_metadata(metadata, name, version)
        with tarfile.open(sdist, "r:gz") as archive:
            member = archive.extractfile(f"{name}-{version}/PKG-INFO")
            if member is None:
                raise ValueError(f"Missing metadata: {sdist}")
            _check_metadata(member.read(), name, version)


def _check_metadata(data: bytes, name: str, version: str) -> None:
    metadata = BytesParser().parsebytes(data)
    if metadata["Name"].replace("-", "_") != name or metadata["Version"] != version:
        raise ValueError(f"Release metadata mismatch: expected {name}=={version}")

    if name == "altk_evolve":
        requirements = [Requirement(value) for value in metadata.get_all("Requires-Dist", [])]
        defaults = [requirement for requirement in requirements if requirement.marker is None]
        if (
            len(defaults) != 1
            or canonicalize_name(defaults[0].name) != "altk-evolve-core"
            or str(defaults[0].specifier) != f"=={version}"
            or defaults[0].url is not None
            or defaults[0].extras != {"server", "cli", "fastembed", "tracing"}
        ):
            raise ValueError("Convenience artifact must require matching core with the default extras")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("version")
    args = parser.parse_args()
    check(args.directory, args.version)
