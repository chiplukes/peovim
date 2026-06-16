# AI Assistant Instructions
<!-- This file provides project context for AI coding assistants (GitHub Copilot, etc.). Not required for normal usage or contribution. -->

## Project Overview

`peovim` is a fast, modern, cross-platform modal text editor written in Python, inspired by Neovim with a clean Python plugin API.

## Project Guidelines

### Documentation
- Check `/notes` folder for technical documentation before starting work
- See `notes/roadmap.md` for planned and future work
- See `notes/python_overview.md` for file listing to avoid duplicating functionality
- Key reference docs: `notes/architecture.md`, `notes/api.md`, `notes/developer_guide.md`, `notes/user_guide.md`, `notes/vim_compatibility.md`, `notes/logging.md`, `notes/plugins.md`, `notes/native_renderer.md`
- Keep durable information in long-term notes organized by topic
- In durable notes, prefer "what it is" and "how it works" over debugging history
- Keep documentation up to date with code changes
- If a new feature has new keymaps, add them to `init.py` and document them in the relevant notes

### Code Style
- This is a uv-managed project: use `uv run` instead of `python` directly
- Line length limit: 120 characters
- Lint with `uv run ruff check <path>`, format with `uv run ruff format <path>`

### Testing
- Check for related test files in `/tests` when modifying code
- Use `uv run pytest tests/ --tb=no -q` for pass/fail summary
- Add tests for new functionality and bug fixes

### Platform
- Primary development on Windows; support Linux where possible
