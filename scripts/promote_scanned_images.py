"""Promote the exact per-architecture Docker archives that passed release scanning."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile

ARCHITECTURES = ("amd64", "arm64")


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def archive_identity(path, architecture):
    with tarfile.open(path) as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
        if len(manifest) != 1:
            raise ValueError("Expected exactly one image in each scanned Docker archive")
        config_bytes = archive.extractfile(manifest[0]["Config"]).read()
        config = json.loads(config_bytes)
    if config.get("os") != "linux" or config.get("architecture") != architecture:
        raise ValueError(f"Archive does not contain linux/{architecture}")
    return "sha256:" + hashlib.sha256(config_bytes).hexdigest()


def attest(directory, architecture, mode):
    result = json.loads((directory / "policy-result.json").read_text())
    if result["mode"] != mode or result["scope"] != f"release-image-core-linux-{architecture}":
        raise ValueError("Scan policy mode or architecture scope does not match")
    if result["packages"] <= 0 or result["installed"] <= 0:
        raise ValueError("Release requires a nonempty scan and independent runtime inventory")
    if mode == "enforce" and result["pending"] != 0:
        raise ValueError("Enforced license policy has unreviewed findings")
    receipt = {
        "architecture": architecture,
        "image_id": archive_identity(directory / "image.tar", architecture),
        "archive_sha256": digest(directory / "image.tar"),
        "policy_sha256": digest(directory / "policy-result.json"),
        "mode": mode,
        "scan_passed": True,
    }
    (directory / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout


def promote(root, repository, tags, run_id, mode):
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*/altk-evolve", repository):
        raise ValueError("Expected the Docker Hub altk-evolve repository")
    if not re.fullmatch(r"[0-9]+-[0-9]+", run_id):
        raise ValueError("Expected GitHub run ID and attempt")
    if not tags or any(
        not tag.startswith(repository + ":")
        or not re.fullmatch(r"[\w][\w.-]{0,127}", tag.removeprefix(repository + ":"))
        or not tag.endswith("-core")
        for tag in tags
    ):
        raise ValueError("Invalid release tags")

    # Validate BOTH archives and receipts before any Docker load, login-side
    # execution, or registry writes. Never rebuild with publishing credentials.
    images = []
    for architecture in ARCHITECTURES:
        directory = root / architecture
        receipt = json.loads((directory / "receipt.json").read_text())
        if receipt.get("scan_passed") is not True or receipt.get("mode") != mode:
            raise ValueError("Missing successful scan in the current policy mode")
        if receipt.get("architecture") != architecture:
            raise ValueError("Architecture receipt mismatch")
        if receipt["archive_sha256"] != digest(directory / "image.tar"):
            raise ValueError("Scanned archive changed after evaluation")
        if receipt["policy_sha256"] != digest(directory / "policy-result.json"):
            raise ValueError("Scan result changed after evaluation")
        image_id = archive_identity(directory / "image.tar", architecture)
        if receipt["image_id"] != image_id:
            raise ValueError("Scanned image configuration changed")
        images.append((architecture, directory, image_id))

    for architecture, directory, image_id in images:
        docker("load", "--input", str(directory / "image.tar"))
        info = json.loads(docker("image", "inspect", image_id))[0]
        if (info["Id"], info["Os"], info["Architecture"]) != (image_id, "linux", architecture):
            raise ValueError("Loaded image does not match the scanned configuration")

    references = []
    for architecture, _, image_id in images:
        temporary_tag = f"{repository}:scan-{run_id}-core-{architecture}"
        docker("tag", image_id, temporary_tag)
        docker("push", temporary_tag)
        repo_digests = json.loads(docker("image", "inspect", temporary_tag))[0]["RepoDigests"]
        matches = [ref for ref in repo_digests if ref.startswith(repository + "@sha256:")]
        if len(matches) != 1 or not re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", matches[0]):
            raise ValueError("Cannot identify the exact pushed image digest")
        references.append(matches[0])
    tag_arguments = [part for tag in tags for part in ("--tag", tag)]
    docker("buildx", "imagetools", "create", *tag_arguments, *references)
    return references


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("attest")
    record.add_argument("directory", type=Path)
    record.add_argument("architecture", choices=ARCHITECTURES)
    record.add_argument("--mode", choices=("report", "enforce"), required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("root", type=Path)
    publish.add_argument("--repository", required=True)
    publish.add_argument("--metadata", type=Path, required=True)
    publish.add_argument("--run-id", required=True)
    publish.add_argument("--mode", choices=("report", "enforce"), required=True)
    args = parser.parse_args()
    if args.command == "attest":
        attest(args.directory, args.architecture, args.mode)
    else:
        tags = json.loads(args.metadata.read_text())["tags"]
        print(json.dumps(promote(args.root, args.repository, tags, args.run_id, args.mode), indent=2))


if __name__ == "__main__":
    main()
