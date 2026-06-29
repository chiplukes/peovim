# Claude Code Instructions
<!-- This file contains instructions for Claude Code (https://claude.ai/code). It is not required for normal editor usage or contribution. -->

# Project Name

This project is a modal editor similar to Neovim, but written in Python. The primary goal for this editor is a fast, modern, and great api for plugins.

### Documentation
- Check `/notes` folder for technical documentation before starting work
- See `notes/roadmap.md` for planned and future work
- See `notes/python_overview.md` for file listing to avoid duplicating functionality
- Key reference docs: `notes/architecture.md`, `notes/api.md`, `notes/developer_guide.md`, `notes/user_guide.md`, `notes/vim_compatibility.md`, `notes/logging.md`, `notes/plugins.md`, `notes/native_renderer.md`, `notes/keys.md`
- Keep durable information in long-term notes organized by topic
- In durable notes, prefer "what it is" and "how it works" over debugging history or step-by-step investigation notes
- keep documentation up to date with code changes
- if a new feature has new keymaps, make sure to add them to init.py and document them in `notes/keys.md` (single source of truth for keybindings)


### Dev Setup (after clone or fresh pull)
- After `uv sync`, run `uvx pre-commit install` to activate commit hooks (ruff, mypy)
- This is a one-time step per clone; skipping it means lint errors can slip into commits

### Code Style
- This is a uv-managed project: use `uv run` instead of `python` directly
- Line length limit: 120 characters
- Lint with `uv run ruff check <path>`, format with `uv run ruff format <path>`

### Testing
- Check for related test files in `/tests` when modifying code
- When running tests, use `uv run pytest <path> --tb=no -q` for pass/fail summary (minimal output)
- Use verbose flags (`-v`, `--tb=short`) only when debugging specific failures

### Platform
- Editor will be cross platform.
- Primary development on Windows; support Linux where possible

## Gotchas and Specific Instructions
* You are the expert.  Please speak up if a request does not make sense and explain why.
*   **Planning**: For any task taking more than 5 minutes, first write a plan in a `/notes/plan_*.md` file before touching any code. Once work is done, fold any durable findings into the appropriate reference doc (e.g. architecture, api, plugins) and delete the plan file. If the task goes sideways, stop and re-plan immediately.
