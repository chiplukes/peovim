# Contributing

## Getting started

```bash
git clone https://github.com/chiplukes/peovim
cd peovim
uv sync --extra dev
```

## Running the editor

```bash
uv run peovim           # empty buffer
uv run peovim file.py   # open a file
```

## Tests

```bash
uv run pytest tests/ --tb=no -q        # pass/fail summary
uv run pytest tests/ --tb=short -q    # with failure details
```

The test suite runs headlessly — no terminal required. Always run tests after changes.

## Lint and format

```bash
uv run ruff check peovim/
uv run ruff format peovim/
```

Line length limit is 120 characters.

## Documentation

Key reference docs live in `notes/`:

| File | Contents |
|------|----------|
| `notes/architecture.md` | Layer model, rendering pipeline, backend protocol |
| `notes/api.md` | Plugin API design decisions and full reference |
| `notes/developer_guide.md` | Startup flow, persistence, event loop, plugin guidance |
| `notes/plugins.md` | Plugin ecosystem analysis and API gaps |
| `notes/vim_compatibility.md` | Vim/Neovim command compatibility reference |

Keep docs updated when changing behavior. If a new feature adds keymaps, document them in `notes/keys.md` (the single source of truth for keybindings).

## Writing plugins

Plugins are plain Python modules. The plugin API is in `peovim/api/`. The public surface is documented in `notes/api.md`. Use `peovim.api` namespaces rather than importing internals — private attribute access is considered technical debt.

A minimal plugin:

```python
# my_plugin.py
def setup(api):
    api.keymap.nmap("<leader>x", ":echo hello<CR>", desc="Say hello")
    api.events.on("buffer_opened", lambda buf: None)
```

Load it from `init.py`:

```python
plugins.load("my_plugin")
```

## Planning

For any change taking more than 5 minutes, write a `notes/plan_*.md` file first. Once the work is done, fold any durable findings into the relevant reference doc and delete the plan file.

## Pull requests

- Keep changes focused — one feature or fix per PR
- Add tests for new behavior
- Update relevant `notes/` docs
- Run lint and tests before submitting
