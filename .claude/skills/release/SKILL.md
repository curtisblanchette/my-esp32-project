---
name: release
description: Create a git tag and GitHub release with generated release notes
disable-model-invocation: true
allowed-tools:
  - Bash(git *)
  - Bash(gh *)
---

# Release Workflow

Create a semantic version tag and GitHub release with auto-generated notes.

## Step 1: Get Current Version

Find the latest version tag:

```bash
git fetch --tags
git tag --sort=-v:refname | head -20
```

Look for semver patterns like v1.2.3 or v0.1.0. If no tags exist, start at v0.1.0.

## Step 2: Analyze Changes Since Last Release

Get commits since last tag:

```bash
git log $(git describe --tags --abbrev=0 2>/dev/null || echo "")..HEAD --oneline
```

If no previous tag, use git log --oneline.

## Step 3: Determine Version Bump

Based on conventional commits:

- BREAKING CHANGE or ! in commit message means Major bump
- feat: commits mean Minor bump
- fix:, chore:, docs:, etc. mean Patch bump

User can override with arguments:
- /release - Auto-detect bump
- /release patch - Force patch
- /release minor - Force minor
- /release major - Force major
- /release v1.5.0 - Specific version

## Step 4: Generate Release Notes

Group commits by type into Features, Bug Fixes, and Other Changes sections.

## Step 5: Confirm With User

Before creating, show:
- Current version (or "No previous releases" if first release)
- New version
- Release notes preview
- Ask for confirmation using AskUserQuestion

## Step 6: Create Tag and Release

1. Create annotated tag:

```bash
git tag -a v1.2.3 -m "Release v1.2.3"
```

2. Push tag:

```bash
git push origin v1.2.3
```

3. Create GitHub release with notes using gh release create

4. Return the release URL to the user

## Arguments

Access via $ARGUMENTS:
- /release - Auto-detect version bump
- /release patch - Bump patch version
- /release minor - Bump minor version
- /release major - Bump major version
- /release v2.0.0 - Set specific version
- /release --draft - Create draft release

## Important

- NEVER delete existing tags
- NEVER force push tags
- ALWAYS use annotated tags (-a flag)
- ALWAYS confirm version with user before creating
- Match existing tag format (with or without v prefix)