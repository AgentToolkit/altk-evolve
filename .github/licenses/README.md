# Dependency license review

[GitHub's dependency-review action](https://github.com/actions/dependency-review-action)
checks dependency changes on pull requests as `license-check`. Newly introduced
or updated dependencies with incompatible licenses fail the check. Results appear
in the job logs and summary.

## Explicit inventories

Before review, the workflow submits **both the exact PR base and head revisions**
using GitHub's
[Component Detection submission action](https://github.com/advanced-security/component-detection-dependency-submission-action).
Only its `UvLock` and `NpmLockfile3` detectors are enabled. All tracked `uv.lock`
and `package-lock.json` files are discovered recursively through the Git tree API,
including future plugin lockfiles. Base lockfiles deleted by the PR are removed
before scanning the head. Both snapshots use the same correlator, and review
runs afterward with explicit base/head SHAs and snapshot-warning retries.

Core, convenience, optional extras and plugin dependencies recorded in those
lockfiles are submitted without installing packages. Platform-specific versions
are retained by the uv detector. A plugin must be included in the shared uv
workspace lockfile or have its own committed lockfile; a standalone pyproject
without a lockfile is not added by this submission step. Plugins in other
repositories need their own workflow. This is a repository-wide policy, not a
separate dependency audit for each wheel or installation profile.

The submission action is pinned to v0.2.0's commit. It downloads the latest
Microsoft Component Detection release at runtime; the scanner binary is not
pinned by that action. The scanner inherits the submission action’s write token; this upstream supply-chain risk remains until the action supports a verified, pinned scanner. GitHub's native dependency graph remains available too.

## Permissions and fork PRs

Dependency submission requires `contents: write`. The privileged job uses
`pull_request_target` restricted to `main` (the trusted default branch) and checks out only its exact PR base revision. Other target branches do not receive this privileged check; changing the trusted branch requires a reviewed workflow change. A
base-branch helper fetches PR lockfile blobs as data; it rejects symlinks, unsafe
paths, truncated inventories and API failures. Both base and head inventories are validated before any blob request, with at most 25 lockfiles and 20 MiB per inventory. Downloaded blobs must match their declared sizes. Superseded runs for the same PR are cancelled. It never checks out PR code or
copies PR scripts, policy, `.env`, or executable configuration. The scanner uses
only the two lockfile detectors, with no dependency installation or build steps.
An empty scan fails. Fork PRs follow the same path and are not silently skipped.

The helper's tests run in a separate workflow on `pull_request` (which defines no `license-check` job) with a read-only token. Policy
and workflow changes take effect only after reaching the trusted base branch.
Consequently the new submission job's first end-to-end Actions validation requires
this workflow and helper to be merged; subsequent PRs exercise it automatically.
Keep the dependency graph enabled in repository settings. To require a passing
review before merging, add `license-check` to branch protection after validation.

## Policy and limitations

The approved list in `dependency-review.yml` is adapted from
[CUGA PR #978](https://github.com/cuga-project/cuga-agent/pull/978), commit
`b7e9f7eb73fe9bf8967ee56cebabf51dea1abc3a`. Its invalid `BSD-0-Clause` entry is
omitted; the correct SPDX identifier `0BSD` is already included. No CUGA-specific
package exceptions are copied. Vulnerability checking remains in the existing
vulnerability workflow. This job enforces the list directly and does not use a
`LICENSE_ENFORCEMENT` variable or post PR comments.

Explicit submission controls the inventory, not license metadata quality. In the
pinned dependency-review v5.0.0 action, unresolved SPDX expressions and incompatible
licenses fail; missing/unknown licenses are reported without blocking. Review
those findings manually. This checks PR changes rather than all existing
licenses, container images, bundled code, license files, our own distributions'
license declarations, or redistribution obligations.

Public repositories can use dependency review without a paid security subscription;
private repositories require the applicable GitHub security plan.
