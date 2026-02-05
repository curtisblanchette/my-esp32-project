---
name: pr
description: Create a pull request with well-formatted title and description
disable-model-invocation: true
allowed-tools:
  - Bash(git *)
  - Bash(gh *)
---

# Pull Request Workflow

Create a well-formatted GitHub pull request.

## Step 1: Understand Current State

Run these in parallel:
- `git status` - Check for uncommitted changes
- `git branch --show-current` - Get current branch name
- `git log origin/main..HEAD --oneline` - See all commits to include
- `git diff origin/main...HEAD --stat` - See files changed

If there are uncommitted changes, ask the user if they want to commit first.

## Step 2: Identify Base Branch

Check which branch to target:
- Default to `main`
- If `main` doesn't exist, try `master`
- User can specify with `/pr base-branch`

## Step 3: Analyze All Changes

Review the full diff against base:
```bash
git diff origin/main...HEAD
```

Understand:
- What features were added
- What bugs were fixed
- What was refactored
- Breaking changes

## Step 4: Write PR Title

Rules:
- Keep under 70 characters
- Use imperative mood
- Match conventional commit style if commits use it
- Be specific but concise

Examples:
- `feat(api): add WebSocket support for real-time updates`
- `fix(auth): resolve token refresh race condition`
- `refactor: migrate database layer to Prisma`

## Step 5: Write PR Description

Use this format:

```markdown
## Summary
- Bullet point summary of changes (1-3 points)
- Focus on WHAT and WHY

## Changes
- List specific changes if helpful
- Group by area/component

## Test plan
- [ ] How to test this PR
- [ ] Specific scenarios to verify

```

## Step 6: Push and Create PR

1. Ensure branch is pushed with tracking:
```bash
git push -u origin HEAD
```

2. Create the PR using HEREDOC for body:
```bash
gh pr create --title "the title" --body "$(cat <<'EOF'
## Summary
...

## Test plan
...

EOF
)"
```

3. Return the PR URL to the user

## Arguments

- `/pr` - Create PR to main
- `/pr develop` - Create PR to specific base branch
- `/pr --draft` - Create as draft PR

Access arguments via `$ARGUMENTS`.

## Important

- NEVER force push 
- NEVER create PR if there are no commits ahead of base
- NEVER include sensitive information in PR description
- If branch isn't pushed, push it first with `-u` flag
