---
name: docs
description: Update README.md and CLAUDE.md to reflect recent code changes
allowed-tools:
  - Read
  - Edit
  - Grep
  - Glob
  - Bash(git diff:*)
  - Bash(git log:*)
---

# Documentation Sync

Update project documentation (README.md and CLAUDE.md) to stay consistent with recent code changes.

## When to Use

Run this skill after completing implementation work to ensure documentation reflects:
- New features or APIs
- Changed file paths or architecture
- Updated dependencies or tools
- Modified commands or workflows
- Removed or deprecated functionality

## Step 1: Analyze Recent Changes

Review what changed since documentation was last updated:

```bash
git log --oneline -20
git diff HEAD~5 --stat
```

Focus on changes to:
- API routes and endpoints
- Environment variables
- Docker/infrastructure config
- Device code and protocols
- Build commands or tooling
- Dependencies (package.json, requirements.txt)

## Step 2: Read Current Documentation

Read both documentation files to understand current state:
- README.md - User-facing documentation
- CLAUDE.md - AI assistant context

## Step 3: Identify Gaps

Compare recent changes against documentation. Look for:
- New endpoints not documented
- Changed file paths in "Key Files" sections
- Updated environment variables
- New or changed commands
- Architecture changes in diagrams
- Outdated technology references (e.g., library swaps)

## Step 4: Update Documentation

Edit both files to reflect current state:

**README.md updates:**
- Architecture diagrams (mermaid)
- API endpoint tables
- Environment variable lists
- Setup instructions
- Troubleshooting guides

**CLAUDE.md updates:**
- Build commands
- Key files list
- API endpoints summary
- Environment variables
- Architecture overview

## Step 5: Verify Consistency

Ensure README.md and CLAUDE.md are consistent with each other:
- Same endpoint lists
- Same environment variables
- Same file paths
- Same commands

## Guidelines

- Keep changes minimal and focused on accuracy
- Don't add new sections unless necessary
- Match existing formatting and style
- Update version numbers if applicable
- Remove references to deleted features
- Add references to new features

## Output

After updating, summarize what changed:
- Files modified
- Sections updated
- Key changes made