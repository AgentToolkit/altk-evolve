"""Plan license jobs from complete local Git ranges and validate their aggregate."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath


def impacts_image(name):
    path = PurePosixPath(name)
    base = path.name.lower()
    return (
        base
        in {
            "pyproject.toml",
            "uv.lock",
            "uv.toml",
            ".python-version",
            "setup.py",
            "setup.cfg",
            "package.json",
            "package-lock.json",
            "npm-shrinkwrap.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "poetry.lock",
            "pipfile",
            "pipfile.lock",
            ".dockerignore",
            ".containerignore",
            "docker-bake.hcl",
            "docker-bake.json",
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
            "makefile",
            "justfile",
            ".npmrc",
            ".yarnrc.yml",
            "manifest.in",
        }
        or base.startswith("dockerfile")
        or base.endswith(".dockerfile")
        or base.endswith(".dockerignore")
        or (base.startswith("requirements") and base.endswith((".txt", ".in")))
        or name.startswith((".github/workflows/", ".github/actions/", ".github/licenses/", "scripts/"))
    )


def changed_paths(base, head):
    if not all(re.fullmatch(r"[0-9a-f]{40}", value or "") for value in (base, head)):
        raise ValueError("Expected full Git commit IDs")
    result = subprocess.run(["git", "diff", "--no-renames", "--name-only", "-z", base, head, "--"], check=True, capture_output=True)
    return [os.fsdecode(name) for name in result.stdout.split(b"\0") if name]


def plan(event_name, event, diff=changed_paths):
    if event_name == "workflow_dispatch":
        return True
    if event_name == "pull_request":
        pr = event["pull_request"]
        return any(impacts_image(name) for name in diff(pr["base"]["sha"], pr["head"]["sha"]))
    if event_name == "push":
        if event.get("ref") != "refs/heads/main":
            raise ValueError("Only main pushes are supported")
        before = event.get("before")
        if not before or before == "0" * 40:
            return True
        try:
            return any(impacts_image(name) for name in diff(before, event["after"]))
        except (ValueError, subprocess.CalledProcessError):
            # A deleted/unavailable push baseline must never suppress image scanning.
            return True
    raise ValueError(f"Unsupported event: {event_name}")


def aggregate(jobs):
    required = {"plan", "policy", "python", "frontend", "image"}
    if set(jobs) != required:
        raise ValueError("Missing or unexpected license jobs")
    for name in required - {"image"}:
        if jobs[name]["result"] != "success":
            raise ValueError(f"License job {name} failed or did not run")
    image = jobs["plan"].get("outputs", {}).get("image")
    if image not in {"true", "false"}:
        raise ValueError("Missing or invalid image plan")
    expected = "success" if image == "true" else "skipped"
    if jobs["image"]["result"] != expected:
        raise ValueError(f"Image job must be {expected}, got {jobs['image']['result']}")


def select_policy(mode, base=Path(".license-policy-base/.github/licenses"), current=Path(".github/licenses")):
    if mode not in {"report", "enforce"}:
        raise ValueError("Expected report or enforce mode")
    if all((base / name).is_file() for name in ("policy.json", "baseline.json")):
        return base
    if base.exists():
        raise ValueError("Trusted policy is incomplete")
    if mode != "report":
        raise ValueError("Trusted policy missing; first introduction supports report mode only")
    print("Bootstrap report: base has no license policy; proposed policy cannot authorize enforcement.")
    if not all((current / name).is_file() for name in ("policy.json", "baseline.json")):
        raise ValueError("Proposed policy is incomplete")
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "aggregate", "policy"])
    args = parser.parse_args()
    if args.command == "aggregate":
        aggregate(json.loads(os.environ["JOB_RESULTS"]))
        return
    if args.command == "plan":
        result = str(plan(os.environ["GITHUB_EVENT_NAME"], json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()))).lower()
        output = f"image={result}"
    else:
        output = f"directory={select_policy(os.environ.get('LICENSE_MODE', 'report'))}"
    print(output)
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write(output + "\n")


if __name__ == "__main__":
    main()
