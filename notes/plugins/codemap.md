# Codemap Plugin

`peovim.plugins.codemap` — repo-committed navigation waypoints backed by
in-code anchor comments.

The key idea: anchors live **inside the source code as comments**, so they
travel with the code through edits, refactors, and git history.  A single
`.codemap.md` file checked into the repo ties together a human-readable
table of contents with those anchors.

---

## Quick start for a new codebase

1. **Load the plugin** in `~/.config/peovim/init.py`:

   ```python
   plugins.load('peovim.plugins.codemap')
   ```

2. **Add keymaps** (the plugin registers `<Plug>` names; wire them however
   you like):

   ```python
   keymap.ngroup("<leader>M", "Codemap")
   keymap.nmap("<leader>Mm", "<Plug>CodemapPicker",       desc="Codemap: picker")
   keymap.nmap("<leader>Mt", "<Plug>CodemapToggle",       desc="Codemap: toggle sidebar")
   keymap.nmap("<leader>Mi", "<Plug>CodemapInsertAnchor", desc="Codemap: insert anchor")
   keymap.nmap("<leader>Mo", "<Plug>CodemapOpenFile",     desc="Codemap: open map file")
   ```

3. **Create the map file** — press `<leader>Mo` to open `.codemap.md` (the
   plugin creates a starter template if the file doesn't exist yet).

4. **Insert anchors** — place the cursor on any interesting line in the
   source and press `<leader>Mi`.  The plugin appends a comment like
   `// cm:a1b2c3` to that line and shows a notification with the ID.

5. **Edit `.codemap.md`** to add an entry for each anchor:

   ```markdown
   ## Section Name
   - [Human label](cm://a1b2c3) — one sentence describing why this matters
   ```

6. **Navigate** with `<leader>Mm` (picker) or `<leader>Mt` (sidebar).

---

## Anchor comment format

The plugin auto-detects the comment style from the buffer's filetype:

| Filetypes | Anchor comment |
|-----------|---------------|
| Verilog, C/C++, JS/TS, Go, Rust, Java, … | `// cm:a1b2c3` |
| Python, Ruby, Shell, YAML, TOML, … | `# cm:a1b2c3` |
| Lua, SQL, Haskell | `-- cm:a1b2c3` |

The anchor ID is **6 lowercase hex characters** generated randomly on each
insertion.  IDs are project-wide unique in practice (16 million possibilities).

---

## `.codemap.md` format

```markdown
# Project Title — Codebase Map

## Section Heading
- [Label](cm://a1b2c3) — description
- [Another Label](cm://d4e5f6)

## Another Section
- [Something](cm://g7h8i9) — notes go here
```

Rules:
- Top-level `# Title` line is optional and purely cosmetic.
- `## Heading` lines define sections (shown as collapsed/expanded nodes in the
  sidebar).
- List entries must be `- [label](cm://ID)` — the URL scheme is `cm://`.
- An optional description may follow a `—` or `-` separator on the same line.
- The file is parsed in document order, so sections and entries appear in the
  sidebar and picker exactly as written.

---

## Sidebar panel

Press `<leader>Mt` to open the Codemap sidebar.  See [keys.md](keys.md) for the
full key reference.  The sidebar shows a live float preview of the anchor's
surrounding code as you move the cursor.

---

## Picker

`<leader>Mm` opens a fuzzy picker over all `.codemap.md` entries.  Each item
shows `[Section] Label — description  (rel/path:line)`.  A preview pane
shows the surrounding source.  Press `<CR>` to jump.

---

## Scanner

The plugin walks the project root (detected via `.git`, `pyproject.toml`,
etc.) and builds an index of all `cm:ID` occurrences in source files.
Only known code file extensions are scanned; common noise directories
(`.git`, `__pycache__`, `node_modules`, etc.) are skipped.

The index is rebuilt on each `refresh()` call (sidebar open, `:Codemap` command,
or `<leader>Mo` / `R` in the sidebar).  There is no continuous file-watch —
refresh is cheap because the scanner is a single-pass directory walk.

If an anchor ID in `.codemap.md` is not found in the project files, the sidebar
entry is shown in muted red and the picker shows `(anchor not found)`.

---

## Applying to a new codebase

Suggested workflow for onboarding a new project:

1. Open the project root in peovim.
2. Press `<leader>Mo` — creates a starter `.codemap.md`.
3. Navigate to the most important files and lines:
   - Entry point / `main()`
   - Central data structures / models
   - Key algorithms or state machines
   - Public API surfaces
   - Non-obvious design decisions
4. On each important line press `<leader>Mi` to insert an anchor.
5. Open `.codemap.md` and add descriptive entries for each anchor,
   grouping them into logical sections ordered for a reader who is new to
   the codebase.
6. Commit both the annotated source files and `.codemap.md` together.

---

## Source

Plugin implementation: [`peovim/plugins/codemap.py`](../peovim/plugins/codemap.py)

See also: [plugins.md](plugins.md) for the broader plugin ecosystem, and
[developer_guide.md](developer_guide.md) for the layer model and startup flow.
