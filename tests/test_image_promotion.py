"""Release promotion must use the scanned bytes on both platforms, without a rebuild."""

import io
import json
import tarfile

import pytest

from scripts import promote_scanned_images as promotion

pytestmark = pytest.mark.unit


def make_archive(directory, architecture, mode="report", pending=2):
    directory.mkdir(parents=True)
    files = {
        "manifest.json": json.dumps([{"Config": "config.json", "RepoTags": ["local:scan"], "Layers": []}]).encode(),
        "config.json": json.dumps({"os": "linux", "architecture": architecture}).encode(),
    }
    with tarfile.open(directory / "image.tar", "w") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    (directory / "policy-result.json").write_text(
        json.dumps(
            {
                "mode": mode,
                "scope": f"release-image-core-linux-{architecture}",
                "packages": 12,
                "installed": 8,
                "pending": pending,
            }
        )
    )
    return promotion.attest(directory, architecture, mode)


@pytest.fixture
def scanned(tmp_path):
    for architecture in promotion.ARCHITECTURES:
        make_archive(tmp_path / architecture, architecture)
    return tmp_path


def publish(root):
    return promotion.promote(
        root, "example/altk-evolve", ["example/altk-evolve:1.2.3-core", "example/altk-evolve:latest-core"], "123-1", "report"
    )


def test_successful_promotion_uses_loaded_ids_and_pushed_digests(scanned, monkeypatch):
    calls = []
    receipts = {arch: json.loads((scanned / arch / "receipt.json").read_text()) for arch in promotion.ARCHITECTURES}

    def docker(*args):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            for architecture, receipt in receipts.items():
                if args[2] == receipt["image_id"]:
                    return json.dumps([{"Id": args[2], "Os": "linux", "Architecture": architecture}])
            suffix = "a" if args[2].endswith("amd64") else "b"
            return json.dumps([{"RepoDigests": ["example/altk-evolve@sha256:" + suffix * 64]}])
        return ""

    monkeypatch.setattr(promotion, "docker", docker)
    references = publish(scanned)
    assert references == ["example/altk-evolve@sha256:" + letter * 64 for letter in ("a", "b")]
    assert [call for call in calls if call[0] == "tag"] == [
        ("tag", receipts[arch]["image_id"], f"example/altk-evolve:scan-123-1-core-{arch}") for arch in promotion.ARCHITECTURES
    ]
    assert calls[-1] == (
        "buildx",
        "imagetools",
        "create",
        "--tag",
        "example/altk-evolve:1.2.3-core",
        "--tag",
        "example/altk-evolve:latest-core",
        *references,
    )
    assert all(call[0] not in ("build", "run") for call in calls)
    assert max(i for i, call in enumerate(calls) if call[0] == "load") < min(i for i, call in enumerate(calls) if call[0] == "push")


@pytest.mark.parametrize("failure", ["archive", "policy", "missing", "mode", "architecture", "status"])
def test_invalid_second_platform_prevents_all_docker_operations(scanned, monkeypatch, failure):
    directory = scanned / "arm64"
    if failure == "archive":
        with (directory / "image.tar").open("ab") as stream:
            stream.write(b"changed")
    elif failure == "policy":
        (directory / "policy-result.json").write_text("{}")
    elif failure == "missing":
        (directory / "receipt.json").unlink()
    else:
        receipt = json.loads((directory / "receipt.json").read_text())
        receipt[{"mode": "mode", "architecture": "architecture", "status": "scan_passed"}[failure]] = {
            "mode": "enforce",
            "architecture": "amd64",
            "status": False,
        }[failure]
        (directory / "receipt.json").write_text(json.dumps(receipt))
    calls = []
    monkeypatch.setattr(promotion, "docker", lambda *args: calls.append(args))
    with pytest.raises((ValueError, FileNotFoundError)):
        publish(scanned)
    assert calls == []


def test_report_findings_are_not_falsely_described_as_enforced_clearance(tmp_path):
    receipt = make_archive(tmp_path / "report", "amd64", pending=4)
    assert receipt["mode"] == "report"
    assert receipt["scan_passed"] is True
    with pytest.raises(ValueError, match="unreviewed"):
        make_archive(tmp_path / "enforce", "amd64", mode="enforce", pending=4)


def test_archive_architecture_must_match_scan_scope(tmp_path):
    directory = tmp_path / "amd64"
    make_archive(directory, "amd64")
    with pytest.raises(ValueError, match="linux/arm64"):
        promotion.archive_identity(directory / "image.tar", "arm64")


def test_loaded_configuration_mismatch_prevents_push(scanned, monkeypatch):
    calls = []

    def docker(*args):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": "sha256:wrong", "Os": "linux", "Architecture": "amd64"}])
        return ""

    monkeypatch.setattr(promotion, "docker", docker)
    with pytest.raises(ValueError, match="Loaded image"):
        publish(scanned)
    assert not any(call[0] in ("push", "tag", "buildx") for call in calls)
