---
name: commit
description: Create git commits with properly styled messages based on project conventions
disable-model-invocation: true
allowed-tools:
  - Bash(git *)
---

# Commit Workflow

Create a well-formatted commit based on the project's existing conventions.

## Step 1: Analyze Project Commit Style

Run `git log --oneline -20` to detect the commit message style:

**Conventional Commits** (look for patterns like `feat:`, `fix:`, `chore:`):
- `feat(scope): description` - New feature
- `fix(scope): description` - Bug fix
- `docs(scope): description` - Documentation
- `style(scope): description` - Formatting, no code change
- `refactor(scope): description` - Code restructuring
- `test(scope): description` - Adding tests
- `chore(scope): description` - Maintenance tasks

**Other styles** - Match the existing pattern (capitalized, imperative, etc.)

## Step 2: Review Changes

Run these commands to understand what changed:
- `git status` - See all modified/untracked files
- `git diff --staged` - View staged changes
- `git diff` - View unstaged changes

## Step 3: Determine Commit Type

Based on the changes:
- New functionality → `feat`
- Bug fix → `fix`
- Documentation only → `docs`
- Formatting/linting → `style`
- Code restructure without behavior change → `refactor`
- Adding/updating tests → `test`
- Build, deps, config, tooling → `chore`

## Step 4: Identify Scope

Determine scope from the changed files:
- Single component/module → use its name (e.g., `auth`, `api`, `ui`)
- Multiple related files → use the common area (e.g., `web`, `server`)
- Cross-cutting changes → omit scope or use general term

## Step 5: Write Commit Message

Format: `type(scope): concise description`

Rules:
- Use imperative mood ("add" not "added")
- Lowercase first letter after colon
- No period at end
- Keep under 72 characters
- Focus on WHY and WHAT, not HOW

For multi-line commits with body:
```
type(scope): short summary

Longer explanation if needed. Wrap at 72 characters.
Explain the motivation for the change.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```

## Step 6: Stage and Commit

1. Stage relevant files individually (avoid `git add .`)
2. Create the commit using a HEREDOC for proper formatting:

```bash
git commit -m "$(cat <<'EOF'
type(scope): description

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

3. Run `git status` to verify success

## Important

- NEVER amend existing commits unless explicitly requested
- NEVER force push
- NEVER commit sensitive files (.env, credentials, secrets)
- If pre-commit hooks fail, fix issues and create a NEW commit
