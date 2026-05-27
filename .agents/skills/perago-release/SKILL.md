---
name: perago-release
description: Perago release workflow for preparing, validating, publishing, and verifying new package versions from /Users/lyk/code/perago. Use when Codex is asked to release Perago, bump Perago's version, create a GitHub release/tag, publish to PyPI via the repo's GitHub Actions trusted publishing workflow, verify Read the Docs/PyPI/GitHub Actions release status, or diagnose Perago release automation problems.
---

# Perago Release

## Core Rule

Treat publishing as a live external operation. Do not create or publish a GitHub release unless the user explicitly asks to publish that version in the current turn.

Work from `/Users/lyk/code/perago`. In this repo, shell commands should use the local `rtk` prefix. The release target is `Qiyin-Tech/perago`, default branch `main`.

## Release Shape

- Source version: `src/perago/_version.py`.
- Build metadata: `pyproject.toml` uses dynamic version from `perago._version.__version__`.
- Release tag: `v{version}`, for example `v0.3.0`.
- GitHub Release title: `perago {version}`, for example `perago 0.4.0`.
- Compare link: `https://github.com/Qiyin-Tech/perago/compare/vA.B.C...vX.Y.Z`.
- Publish trigger: `.github/workflows/publish.yml` runs on GitHub Release `published`.
- PyPI publish: GitHub Actions trusted publishing using the `pypi` environment and OIDC `id-token: write`.
- Release notes: Chinese Markdown for an internal enterprise repository, hand-written by Codex after reading the actual diff, PRs, docs, tests, and release context. Use stable Chinese section headings, omit irrelevant sections, do not generate the changelog mechanically from commit subjects, and do not list every direct commit.

## Preflight

1. Confirm repository and cleanliness:

```bash
rtk git status --short --branch
rtk gh repo view --json nameWithOwner,defaultBranchRef,url
rtk gh release list --limit 20
rtk gh run list --workflow Publish --limit 10 --json databaseId,displayTitle,event,headBranch,headSha,status,conclusion,createdAt,updatedAt,url
```

2. Confirm the requested version is new:

```bash
rtk sed -n '1,40p' src/perago/_version.py
rtk git tag --list 'v*' --sort=-v:refname
rtk gh release view "vX.Y.Z"
```

If the tag or release already exists, stop and diagnose. Do not delete or retag public releases unless the user explicitly requests that destructive operation.

3. Inspect release automation before relying on it:

```bash
rtk sed -n '1,140p' .github/workflows/publish.yml
rtk gh api repos/Qiyin-Tech/perago/environments/pypi
```

If the `pypi` environment check returns 404 or the workflow lacks `id-token: write`, fix that before publishing.

## Prepare Version

1. Update only the source version unless the repo has intentionally changed its version scheme:

```python
__version__ = "X.Y.Z"
```

2. Run focused version checks:

```bash
rtk uv run perago --version
rtk uv run python - <<'PY'
import perago
from perago._version import __version__
print(perago.__version__)
print(__version__)
PY
```

3. Build and inspect package metadata:

```bash
rtk rm -rf dist
rtk uv build
rtk python3 -m zipfile -l "dist/perago-X.Y.Z-py3-none-any.whl"
```

Use a temporary virtualenv or `uv run --with dist/...` if needed to verify the built wheel reports the same version through `importlib.metadata.version("perago")`.

## Validate Before Publishing

Run the strongest practical local gate for the size of the release. For normal releases, use:

```bash
rtk uv run pytest -q
rtk uv run python -m compileall -q src/perago tests
rtk uv run --with-requirements docs/requirements.txt sphinx-build -W -b html docs /tmp/perago-docs-release
rtk git diff --check
rtk git diff --cached --check
```

If the release includes Conductor/LakeFS runtime changes and `.env` points at usable live services, also run the tracked smoke scripts. Clean residual worker processes, staging branches, and owned workspaces after any failed live smoke.

## Draft Release Notes

Write the GitHub Release changelog manually. Use commands only to gather facts and context:

```bash
rtk git log --first-parent --oneline "vA.B.C..HEAD"
rtk git diff --stat "vA.B.C..HEAD"
rtk gh pr list --state merged --base main --limit 50 --json number,title,mergeCommit,mergedAt,url
rtk gh release view "vA.B.C" --json tagName,name,publishedAt,url
```

Then synthesize the release notes into a short, reader-facing Chinese changelog. Use stable top-level sections such as:

- `### 新增`
- `### 变更`
- `### 修复`
- `### 文档`
- `### 测试`
- `### 发布验证`

Omit sections that do not apply. Under each section, group changes by product/runtime/documentation/testing themes, explain why the release matters, and mention PR numbers only when they help readers trace larger work. Do not include a "Direct Commits" section or enumerate commit-by-commit details.

Keep the final notes factual and based on the repository state, merged PRs, tests, docs, and release-specific behavior. Do not invent PyPI or RTD success before verifying it.

## Publish

Prefer a reviewed draft, then publish intentionally:

```bash
rtk gh release create "vX.Y.Z" --target main --title "perago X.Y.Z" --notes-file /tmp/perago-X.Y.Z-release.md --draft
rtk gh release view "vX.Y.Z" --json tagName,name,isDraft,isPrerelease,targetCommitish,url
rtk gh release edit "vX.Y.Z" --draft=false
```

Publishing the release triggers `.github/workflows/publish.yml`. Watch the exact run:

```bash
rtk gh run list --workflow Publish --limit 5 --json databaseId,displayTitle,headBranch,status,conclusion,url
rtk gh run watch RUN_ID --exit-status
rtk gh run view RUN_ID --log-failed
```

If the workflow fails after a public release is published, diagnose the run first. For already-public tags/releases, prefer fixing automation and re-running the failed GitHub Actions job when possible. Avoid deleting and recreating public releases unless the user approves that specific recovery path.

## Verify

After the workflow succeeds:

```bash
rtk gh release view "vX.Y.Z" --json tagName,name,publishedAt,targetCommitish,url
rtk python3 - <<'PY'
import json
import urllib.request
data = json.load(urllib.request.urlopen("https://pypi.org/pypi/perago/json", timeout=20))
print(data["info"]["version"])
print(sorted(data["releases"])[-5:])
PY
```

Check Read the Docs separately. A successful GitHub release/PyPI publish does not prove RTD activated the tag version. If a historical tag does not appear in RTD, do not re-push the public tag; use RTD manual activate/sync or the configured RTD integration path.

RTD webhook `ping` only proves the endpoint is reachable; it does not prove a tag version was created, activated, or built. Future tag automation should use RTD `Activate version` for tag SemVer rules.

## Closeout

Report:

- Version, tag, GitHub release URL.
- Publish workflow run URL and conclusion.
- PyPI observed version.
- RTD observed status, including whether manual activation is still needed.
- Local validation commands run and any skipped checks.
