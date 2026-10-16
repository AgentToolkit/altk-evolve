# Dependency license review

[GitHub's dependency-review action](https://github.com/actions/dependency-review-action)
runs on every pull request as `license-check`. It compares the base and head
revisions using GitHub's dependency graph and fails for newly introduced or
updated dependencies with incompatible licenses. Results appear in the job logs
and summary. No application dependency installation or custom scanner is needed.

The approved list lives in `dependency-review.yml`, adapted from
[CUGA PR #978](https://github.com/cuga-project/cuga-agent/pull/978) at commit
`b7e9f7eb73fe9bf8967ee56cebabf51dea1abc3a`. Its invalid `BSD-0-Clause` entry is
omitted: the correct SPDX identifier `0BSD` is already included. Keeping the
invalid entry causes the action's license evaluator to reject even MIT packages.
No CUGA-specific package exceptions are copied.

The job uses read-only permissions and does not post PR comments. Vulnerability
checking is disabled here because `check-vulnerabilities.yaml` handles that
separately. Unlike the previous custom scanner draft, this check enforces the
approved list directly and does not use a `LICENSE_ENFORCEMENT` variable.

## Coverage and limitations

GitHub currently recognizes this repository's `uv.lock` and UI
`package-lock.json`; its exported dependency inventory includes versioned Python
and npm packages. This workflow relies on that native graph, without an additional
dependency submission job. Keep the dependency graph enabled in repository
settings. Public repositories can use dependency review without a paid security
subscription; private repositories require the applicable GitHub security plan.

The policy applies across the repository, not separately to each published
distribution. Core, convenience and optional plugin dependencies share the same
policy when GitHub includes them in the dependency graph. Optional dependencies
recorded in the shared lockfile can be reviewed even when absent from the default
installation. For new plugin manifests or lockfiles, verify their dependency
changes appear in a PR review; plugins in separate repositories need their own
workflow. This check does not establish the dependency closure of each wheel,
extra or installation profile, nor validate our own packages' license metadata.

This is a PR change check, not an audit of the existing dependency inventory,
container images, bundled code, license files, or redistribution obligations.
License results depend on GitHub's metadata. In the pinned v5.0.0 action,
unresolved SPDX expressions fail, while missing/unknown licenses are reported
for manual review and do not block the job. Inspect those findings in the summary.

To require a passing result before merging, add `license-check` to the branch
protection or ruleset required checks after the workflow has run. Changes to the
approved list should receive maintainer review.
