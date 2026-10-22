"""Conservative platform-test selection for PRs, including inherited stack changes."""

import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath


def relevant(path):
    name = PurePosixPath(path).name.lower()
    return (
        path.startswith(
            (
                "platform-integrations/",
                "plugin-source/",
                "tests/platform_integrations/",
                ".github/",
                "scripts/",
                "packages/",
                "tests/fixtures/",
                "tests/helpers/",
            )
        )
        or name
        in {
            "conftest.py",
            "pyproject.toml",
            "uv.lock",
            "uv.toml",
            ".python-version",
            "pytest.ini",
            "tox.ini",
            "setup.cfg",
            "setup.py",
            "manifest.in",
        }
        or name.startswith("requirements")
    )


def changed_paths(base, head):
    if not all(re.fullmatch(r"[0-9a-f]{40}", sha or "") for sha in (base, head)):
        raise ValueError("Expected full Git commit IDs")
    result = subprocess.run(["git", "diff", "--no-renames", "--name-only", "-z", f"{base}...{head}", "--"], check=True, capture_output=True)
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def should_run(event_name, event, diff=changed_paths):
    # Main pushes, releases/reusable calls and manual dispatch always run all tests.
    if event_name != "pull_request":
        return True
    try:
        pr = event["pull_request"]
        # Native stack members must include changes inherited from lower PRs.
        base = pr["stack"]["base"]["sha"] if pr.get("stack") else pr["base"]["sha"]
        return any(relevant(path) for path in diff(base, pr["head"]["sha"]))
    except (KeyError, TypeError, ValueError, OSError, subprocess.CalledProcessError):
        return True


def main():
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        run = should_run(os.environ.get("GITHUB_EVENT_NAME"), event)
    except (KeyError, OSError, ValueError):
        run = True
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"platform={str(run).lower()}\n")
    print(f"Run platform integration tests: {run}")


if __name__ == "__main__":
    main()
