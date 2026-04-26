# iris-vector-graph Development Guidelines

## CRITICAL: External Actions Require Explicit Permission

**NEVER perform any of the following without Tom explicitly saying "file it", "create it", "submit it", "post it", or similar direct instruction:**

- Create GitHub issues (`gh issue create`)
- Create pull requests (`gh pr create`)
- Post to Slack, Teams, or any messaging system
- Send emails
- Create Jira tickets
- Post to any external forum, community site, or public resource
- Push to any remote git repository (including `git push`) unless explicitly asked
- Deploy to any server, cloud, or external service

**Drafting is always OK. Filing/sending/deploying is NEVER OK without explicit permission.**

## Active Technologies

- Python 3.10+ (pyproject.toml `requires-python = ">=3.10"`), ObjectScript (IRIS) + `iris-devtester>=1.14.0`, `pytest>=7.4.0` (042-bucket-group-api)

## Project Structure

```text
src/
tests/
```

## Commands

cd src [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] pytest [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] ruff check .

## Code Style

Python 3.10+ (pyproject.toml `requires-python = ">=3.10"`), ObjectScript (IRIS): Follow standard conventions

## Recent Changes

- 042-bucket-group-api: Added Python 3.10+ (pyproject.toml `requires-python = ">=3.10"`), ObjectScript (IRIS) + `iris-devtester>=1.14.0`, `pytest>=7.4.0`

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
