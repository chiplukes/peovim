# Keybindings Reference

Single source of truth for all keybindings. Other notes link here rather than duplicating tables.

> **Leader key**: `<Space>` in this config. Default is `\`. Set with `options.set("leader", " ")`.
>
> Core Vim motions (`hjkl`, `w/b/e`, `gg/G`, `f/t`, `%`, operators `d/c/y/>/</g~/gu/gU`, text objects, visual mode) are standard and not listed here — see `:help motion` or any Vim reference.

---

## How to Remap

Every remappable binding has a `<Plug>Name` in the table below. Use `keymap.nmap` / `keymap.imap` / `keymap.vmap` in `init.py` to wire it to any key. Remove a default binding with `keymap.nunmap`.

```python
# Example: move the file picker off <leader>ff to <leader>sf
keymap.nunmap("<leader>ff")
keymap.nmap("<leader>sf", "<Plug>PickerFindFiles", desc="Find files")

# Example: move git status panel from <leader>gs to <leader>pg
keymap.nunmap("<leader>gs")
keymap.nmap("<leader>pg", "<Plug>GitsignsStatusPanel", desc="Git status panel")

# Example: move outline from <leader>o to <leader>po
keymap.nunmap("<leader>o")
keymap.nmap("<leader>po", "<Plug>OutlineToggle", desc="Outline sidebar")

# Example: move diff group from <leader>c* to <leader>d*
keymap.nunmap("<leader>c1")
keymap.nmap("<leader>d1", "<Plug>CompareSelect1", desc="Compare file 1")
# ... (repeat for each diff binding)

# Example: remap copilot accept from <A-Tab> to <C-y>
keymap.imap("<C-y>", copilot.accept, desc="Accept Copilot suggestion")

# Bindings that use direct lambdas (no <Plug>) cannot be remapped by name;
# just define a new keymap pointing to the same lambda.
```

---

## Search and Navigation

### Inline search (built-in)

| Key | Action |
|-----|--------|
| `/` | Search forward |
| `?` | Search backward |
| `n` / `N` | Next / previous match |
| `*` | Search word under cursor forward |
| `#` | Search word under cursor backward |
| `<Esc>` (normal) | Clear search highlight |

### Jump list (built-in)

| Key | Action |
|-----|--------|
| `<C-o>` | Jump back (cross-file) |
| `<C-i>` | Jump forward (cross-file) |

### Files and buffers (built-in)

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<C-^>` | `EditorAltFile` | Toggle alternate (last visited) file |
| `gf` | — | Go to file under cursor |

---

## Picker / Fuzzy Search

Requires `peovim.plugins.picker`. Default bindings use `<leader>ff/fb/fg`; remapped here to the `<leader>s` group.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>sf` | `PickerFindFiles` | Find files in project |
| `<leader>sr` | `PickerRecentFiles` | Recent files |
| `<leader>sb` | `PickerFindBuffers` | Open buffers |
| `<leader>sg` | `PickerLiveGrep` | Live grep |
| `<leader>sw` | `PickerWordGrep` | Grep word under cursor |
| `<leader>s/` | `PickerBufferLines` | Search lines in current buffer |
| `<leader>sd` | `PickerDiagnostics` | Search diagnostics |
| `<leader>sp` | `PickerCommands` | Search / run ex commands |

Remapping example (from defaults to `<leader>s` group):
```python
keymap.nunmap("<leader>ff")
keymap.nunmap("<leader>fb")
keymap.nunmap("<leader>fg")
keymap.ngroup("<leader>s", "Search")
keymap.nmap("<leader>sf", "<Plug>PickerFindFiles",   desc="Find files")
keymap.nmap("<leader>sb", "<Plug>PickerFindBuffers", desc="Find buffers")
keymap.nmap("<leader>sg", "<Plug>PickerLiveGrep",    desc="Live grep")
```

---

## Windows and Splits

### Split commands (ex commands)

| Command | Action |
|---------|--------|
| `:split` / `:sp` | Horizontal split |
| `:vsplit` / `:vs` | Vertical split |
| `:close` / `:cl` | Close current window |
| `:only` | Close all other windows |

### Window focus and resize

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<C-w>h/j/k/l` | — | Move focus between windows |
| `<C-w>s` | — | Horizontal split |
| `<C-w>v` | — | Vertical split |
| `<C-w>q` | — | Close current window |
| `<C-w>w` | — | Cycle to next window |
| `<C-w>=` | — | Equalize all window sizes |
| `<C-w>o` | — | Close all other windows |
| `<C-w><` / `<C-w>>` | — | Shrink / grow active window width |
| `<C-w>-` / `<C-w>+` | — | Shrink / grow active window height |
| `<leader>wv` | `WinVSplit` | Vertical split |
| `<leader>wc` | `WinClose` | Close current window |
| `<leader>wf` | `WinOnly` | Close all other windows |
| `<leader>we` | `WinEqualize` | Equalize all window sizes (reset to 50/50) |

---

## Sidebar and Bottom Panel Navigation

### Global navigation keys

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<A-h>` | `SidebarFocusLeft` | Focus sidebar (or window left when sidebar hidden) |
| `<A-l>` | `SidebarFocusRight` | Focus editor (or window right) |
| `<A-j>` | `SidebarNextPanel` | Next sidebar panel; if bottom panel is visible and unfocused, focuses it instead |
| `<A-k>` | `SidebarPrevPanel` | Previous sidebar panel (or window up when editor focused) |
| `<A-p>` | `BottomPanelToggle` | Toggle bottom panel |

### Sidebar-internal keys (while sidebar is focused)

| Key | Action |
|-----|--------|
| `[` | Shrink sidebar width |
| `]` | Grow sidebar width |
| `q` / `<Esc>` | Close sidebar |

### Bottom panel-internal keys (while panel is focused)

| Key | Action |
|-----|--------|
| `<Esc>` / `<A-k>` | Blur panel, return focus to editor (panel stays visible) |
| `q` | Hide panel |
| `[` / `]` | Shrink / grow panel height |
| `<` / `>` | Previous / next tab |

### Output tab keys (while output tab is active and focused)

| Key | Action |
|-----|--------|
| `j` / `k` | Move cursor down / up |
| `<C-d>` / `<C-u>` | Scroll down / up 10 lines |
| `g` / `G` | Jump to top / bottom |
| `v` / `V` | Toggle visual line selection; cursor line brightens as the active end; `j`/`k` extend the selection |
| `y` | Yank selection to clipboard (`+` register) |
| `Y` | Yank **all** lines to clipboard |
| `c` | Clear output |


---

## Editing Utilities

### Comments (requires `peovim.plugins.commentary`)

| Key | `<Plug>` | Mode | Action |
|-----|---------|------|--------|
| `gcc` | `CommentaryLine` | Normal | Toggle comment on current line |
| `gcj` | `CommentaryDown` | Normal | Comment current and next line |
| `gck` | `CommentaryUp` | Normal | Comment previous and current line |
| `gc` | `CommentaryVisual` | Visual | Toggle comments on selection |

### Surround (requires `peovim.plugins.surround`)

| Key | Action |
|-----|--------|
| `ys{motion}{char}` | Add surround character around motion |
| `cs{old}{new}` | Change surrounding character |
| `ds{char}` | Delete surrounding character |

These use their own internal key parsing and are not remappable via `<Plug>`.

### Align (requires `peovim.plugins.align`)

| Key | `<Plug>` | Mode | Action |
|-----|---------|------|--------|
| `ga` | `AlignCharPrompt` | Visual | Align selected lines on a character |
| `gA` | `AlignRegexPrompt` | Visual | Align selected lines on a regex |

### Visual block

| Key | Action |
|-----|--------|
| `<C-v>` | Enter visual block mode (default) |
| `<C-q>` | Enter visual block mode (remapped here; use if terminal captures `<C-v>`) |
| `I` (block) | Insert text at left edge of block on all lines |
| `A` (block) | Append text at right edge of block on all lines |
| `r` / `~` / `u` / `U` / `gu` / `gU` (block) | Replace or change case across block |
| `>` / `<` (block) | Indent / outdent covered lines |
| `O` (block) | Move to other corner on current row |
| `p` / `P` (block register) | Paste block register as rectangle |
| `gv` | Reselect last visual selection (including block) |

The `<C-q>` → `<C-v>` remapping for visual block:
```python
keymap.nmap("<C-q>", "<C-v>", desc="Visual block mode")
```

### Misc editing

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>pr` | `EditorPasteYank` | Paste from yank register `"0` (unaffected by deletes) |
| `<leader><leader>` | `EditorRepeat` | Repeat last `remember()`-wrapped command |

---

## File Information

Requires `peovim.plugins.editor_utils`. Default bindings use `<leader>lf*`; remapped here.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>li` | `EditorFileInfo` | Show file info (path, size, type) |
| `<leader>lc` | `EditorCopyPath` | Copy full path to clipboard |
| `<leader>lr` | `EditorCopyRel` | Copy relative path to clipboard |

Remapping example (from defaults):
```python
keymap.nunmap("<leader>lf")
keymap.nunmap("<leader>lfc")
keymap.nunmap("<leader>lfr")
keymap.ngroup("<leader>l", "Location/File")
keymap.nmap("<leader>li", "<Plug>EditorFileInfo", desc="File info")
keymap.nmap("<leader>lc", "<Plug>EditorCopyPath",  desc="Copy full path")
keymap.nmap("<leader>lr", "<Plug>EditorCopyRel",   desc="Copy relative path")
```

---

## Which-Key

Requires `peovim.plugins.which_key`. Shows all leader bindings in a float.

| Key | Action |
|-----|--------|
| `<leader>?` | Show all leader key bindings |

---

## Plugin: LSP

Requires `peovim.plugins.lsp`. The LSP plugin registers most of these by default; a few are wired here via direct lambda calls.

### Hover / Help

| Key | `<Plug>` / call | Action |
|-----|----------------|--------|
| `K` | `LspHover` | Hover documentation at cursor |
| `<C-k>` (insert) | `LspSignatureHelp` | Signature help |

Focused hover floats: `j`/`k` to scroll, `q`/`<Esc>` to close, `y` to yank content.

### Navigation

| Key | `<Plug>` / call | Action |
|-----|----------------|--------|
| `gd` | `LspDefinition` | Go to definition |
| `<leader>cgi` | `lsp.implementation()` | Go to implementation |
| `<leader>cgt` | `lsp.type_definition()` | Go to type definition |
| `<leader>gr` | `lsp.references()` | Find references |
| `go` | `LspDocumentSymbols` | Document symbols |
| `<leader>csd` | `LspDocumentSymbols` | Document symbols (leader variant) |
| `<leader>csw` | `LspWorkspaceSymbols` | Workspace symbols for word under cursor |

### Editing

| Key | `<Plug>` / call | Action |
|-----|----------------|--------|
| `<leader>ca` | `lsp.code_actions()` | Code actions |
| `<leader>rn` | `LspRename` | Rename symbol |
| `<leader>ci` | `lsp.toggle_inlay_hints()` | Toggle inlay hints |
| `<leader>F` | `FormatterFormat` | Format buffer |
| `<C-n>` (insert) | `LspComplete` | Trigger completion popup |

### Diagnostics

| Key | `<Plug>` / call | Action |
|-----|----------------|--------|
| `]d` | `lsp.goto_next_diag()` | Next diagnostic |
| `[d` | `lsp.goto_prev_diag()` | Previous diagnostic |
| `ge` | `lsp.goto_next_diag()` | Next diagnostic (alternate) |
| `<leader>c.d` | `LspDiagDetail` | Diagnostic detail float |
| `<leader>cD` | `DiagnosticsPanel` | Toggle diagnostics sidebar |

### Sidebars

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>cR` | `ReferencesPanel` | Toggle references sidebar for symbol under cursor |
| `<leader>csW` | `WorkspaceSymbolsPanel` | Toggle workspace symbols sidebar |

### Server management

Use ex commands `:LspInfo` and `:LspRestart` — no default key bindings.

### Remapping LSP bindings

LSP plug names use the `Lsp` prefix. Example:
```python
# Move references from <leader>gr to <leader>cgr
keymap.nunmap("<leader>gr")
keymap.nmap("<leader>cgr", lambda: lsp.references(), desc="Find references")

# Wire hover to a leader key as well
keymap.nmap("<leader>ch", "<Plug>LspHover", desc="Hover docs")
```

---

## Plugin: Diff / Compare

Requires `peovim.plugins.compare`. Default bindings use `<leader>c*`; remapped here to `<leader>d` to avoid conflict with the Code group.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>d1` | `CompareSelect1` | Select current file as diff target 1 |
| `<leader>d2` | `CompareSelect2` | Select current file as diff target 2 |
| `<leader>dc` | `CompareSelected` | Launch side-by-side diff |
| `<leader>dj` | `CompareNextDiff` | Jump to next diff block |
| `<leader>dk` | `ComparePrevDiff` | Jump to previous diff block |
| `<leader>ds` | `CompareStop` | Stop diff session and clear decorations |
| `<leader>dm12` | `CompareMerge12` | Merge active diff block left → right |
| `<leader>dm21` | `CompareMerge21` | Merge active diff block right → left |
| `]c` | `CompareNextDiff` | Next diff block (Vim-style alias) |
| `[c` | `ComparePrevDiff` | Previous diff block (Vim-style alias) |

Remapping example (moving off the default `<leader>c*` prefix):
```python
for _k in ("<leader>c1","<leader>c2","<leader>cc","<leader>cj","<leader>ck","<leader>cs"):
    keymap.nunmap(_k)
keymap.ngroup("<leader>d", "Diff")
keymap.nmap("<leader>d1",   "<Plug>CompareSelect1",  desc="Compare file 1")
keymap.nmap("<leader>d2",   "<Plug>CompareSelect2",  desc="Compare file 2")
keymap.nmap("<leader>dc",   "<Plug>CompareSelected", desc="Compare selected files")
keymap.nmap("<leader>dj",   "<Plug>CompareNextDiff", desc="Next diff")
keymap.nmap("<leader>dk",   "<Plug>ComparePrevDiff", desc="Prev diff")
keymap.nmap("<leader>ds",   "<Plug>CompareStop",     desc="Stop compare")
keymap.nmap("<leader>dm12", "<Plug>CompareMerge12",  desc="Merge left→right")
keymap.nmap("<leader>dm21", "<Plug>CompareMerge21",  desc="Merge right→left")
keymap.nmap("]c", "<Plug>CompareNextDiff", desc="Next diff")
keymap.nmap("[c", "<Plug>ComparePrevDiff", desc="Prev diff")
```

---

## Plugin: Explorer

Requires `peovim.plugins.explorer`.

| Key | `<Plug>` | Context | Action |
|-----|---------|---------|--------|
| `<leader>e` | `ExplorerToggle` | Normal | Toggle file explorer sidebar |
| `a` | — | Explorer focused | Create file or directory (end path with `/` for dir) |
| `r` | — | Explorer focused | Rename selected entry |
| `d` | — | Explorer focused | Delete selected entry (with confirmation) |
| `c` | — | Explorer focused | Copy selected entry |
| `C` | — | Explorer focused | Mark selected entry for move |
| `p` | — | Explorer focused | Paste into selected directory |
| `R` | — | Explorer focused | Refresh tree |
| `<CR>` | — | Explorer focused | Open selected file |

Git-backed explorer shows `+` (new), `~` (modified), `!` (deleted) prefixes on entries.

---

## Plugin: Git Signs

Requires `peovim.plugins.gitsigns`. Shows git change gutters and supports hunk navigation.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `]c` | `GitsignsNextHunk` | Jump to next git hunk |
| `[c` | `GitsignsPrevHunk` | Jump to previous git hunk |
| `<leader>gs` | `GitsignsStatusPanel` | Toggle git status panel |

> **Note:** `]c` / `[c` are also the default next/prev diff block keys in the compare plugin. If both plugins are loaded, whichever is loaded last wins for those keys. Remap one to avoid the conflict.

Git panel remapping example:
```python
keymap.nunmap("<leader>gs")   # remove default
keymap.nmap("<leader>pg", "<Plug>GitsignsStatusPanel", desc="Git status panel")
```

### Git panel-local keys (while git panel is focused)

| Key | Action |
|-----|--------|
| `?` | Show key guide |
| `R` | Refresh panel |
| `c` | Create branch |
| `s` (on branch row) | Check out selected branch |
| `m` (on branch row) | Merge selected branch into current |
| `f` | Fetch from tracked remote |
| `p` | Pull from tracked remote/branch |
| `P` | Push to tracked remote/branch |
| `a` (on status row) | Stage file |
| `u` (on status row) | Unstage file |
| `x` (on status row) | Discard file changes |
| `d` (on status row) | Diff file against working tree |
| `l` | Open git log browser for current branch |
| `<CR>` | Open selected file |

---

## Plugin: Markers

Requires `peovim.plugins.markers`.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `mn` | `MarkerNext` | Jump to next marker in active group |
| `mp` | `MarkerPrev` | Jump to previous marker in active group |
| `me` | `MarkerText` | Edit annotation for marker at cursor |
| `<leader>ma` | `MarkerAdd` | Add marker at cursor to active group |
| `<leader>md` | `MarkerDelete` | Delete marker at cursor from active group |
| `<leader>mv` | `MarkerView` | Toggle marker groups sidebar |
| `<leader>mgc` | `MarkerGroupCreate` | Create marker group |
| `<leader>mgs` | `MarkerGroupSelect` | Select active marker group |
| `<leader>mgr` | `MarkerGroupRename` | Rename active marker group |
| `<leader>mgd` | `MarkerGroupDelete` | Delete active marker group |

Marker data is stored per-project in `.peovim/markers.json` when inside a detected project root (`.git`, `pyproject.toml`, `setup.py`, `Cargo.toml`).

### Marker sidebar-local keys

| Key | Action |
|-----|--------|
| `j` / `k` | Navigate |
| `<CR>` | Jump to selected marker |
| `g` | Go to marker in active editor window |
| `e` | Edit annotation for selected marker |

---

## Plugin: Session / Project

Requires `peovim.plugins.session`.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>qs` | `SessionSave` | Save current session |
| `<leader>qr` | `SessionRestore` | Restore last session |

Remapping example (moving to a `<leader>P` group):
```python
keymap.nunmap("<leader>qs")
keymap.nunmap("<leader>qr")
keymap.ngroup("<leader>P", "Project/Session")
keymap.nmap("<leader>Ps", "<Plug>SessionSave",    desc="Save session")
keymap.nmap("<leader>Pr", "<Plug>SessionRestore", desc="Restore session")
```

---

## Plugin: Codemap

Requires `peovim.plugins.codemap`. See [codemap.md](codemap.md) for full workflow.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>Mm` | `CodemapPicker` | Open codemap fuzzy picker |
| `<leader>Mt` | `CodemapToggle` | Toggle codemap sidebar |
| `<leader>Mi` | `CodemapInsertAnchor` | Insert anchor comment at cursor |
| `<leader>Mo` | `CodemapOpenFile` | Open `.codemap.md` file |
| `<leader>Mg` | `CodemapGotoAnchor` | Jump to anchor under cursor |

Default mappings from codemap docs (for your own wiring):
```python
keymap.ngroup("<leader>M", "Codemap")
keymap.nmap("<leader>Mm", "<Plug>CodemapPicker",       desc="Codemap: picker")
keymap.nmap("<leader>Mt", "<Plug>CodemapToggle",       desc="Codemap: toggle sidebar")
keymap.nmap("<leader>Mi", "<Plug>CodemapInsertAnchor", desc="Codemap: insert anchor")
keymap.nmap("<leader>Mo", "<Plug>CodemapOpenFile",     desc="Codemap: open map file")
keymap.nmap("<leader>Mg", "<Plug>CodemapGotoAnchor",   desc="Codemap: goto anchor")
```

### Codemap sidebar-local keys

| Key | Action |
|-----|--------|
| `j` / `k` | Move cursor |
| `<CR>` | Jump to anchor location |
| `<Space>` / `h` / `l` | Collapse / expand section |
| `R` | Re-scan and refresh |
| `q` / `<Esc>` | Close sidebar |

---

## Plugin: Outline

Requires `peovim.plugins.outline`. Default binding is `<leader>o`; remapped here to `<leader>po`.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>po` | `OutlineToggle` | Toggle document outline sidebar |

```python
keymap.nunmap("<leader>o")
keymap.nmap("<leader>po", "<Plug>OutlineToggle", desc="Outline sidebar")
```

### Outline sidebar-local keys

| Key | Action |
|-----|--------|
| `j` / `k` | Navigate symbols |
| `<CR>` | Jump to symbol location |
| `R` | Refresh outline |

---

## Plugin: References Panel

Requires `peovim.plugins.references_panel`.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>cR` | `ReferencesPanel` | Toggle references sidebar for symbol under cursor |

### References sidebar-local keys

| Key | Action |
|-----|--------|
| `j` / `k` | Navigate |
| `<CR>` | Jump to reference location |
| `R` | Refresh references for current symbol |

---

## Plugin: Diagnostics Panel

Requires `peovim.plugins.diagnostics_panel`.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>cD` | `DiagnosticsPanel` | Toggle diagnostics sidebar |

### Diagnostics sidebar-local keys

| Key | Action |
|-----|--------|
| `j` / `k` | Navigate |
| `<CR>` | Jump to diagnostic location |
| `R` | Refresh diagnostics list |

---

## Plugin: Workspace Symbols

Requires `peovim.plugins.workspace_symbols`.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>csW` | `WorkspaceSymbolsPanel` | Toggle workspace symbols sidebar (word under cursor) |

### Workspace symbols sidebar-local keys

| Key | Action |
|-----|--------|
| `j` / `k` | Navigate |
| `<CR>` | Jump to symbol location |
| `/` | Prompt for new workspace symbol query |
| `R` | Refresh current query |

---

## Plugin: Flash Jump

Requires `peovim.plugins.flash`.

| Key | `<Plug>` | Mode | Action |
|-----|---------|------|--------|
| `s` | `FlashJump` | Normal, Visual | Type 2 chars, press jump label |

---

## Plugin: Fquick (Session File Navigator)

Requires `peovim.plugins.fquick`. Navigate recently-opened session files.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `fh` | `FquickOlder` | Older session file |
| `fl` | `FquickNewer` | Newer session file |
| `fj` | `FquickSessionPickerDown` | Open session files picker (down) |
| `fk` | `FquickSessionPickerUp` | Open session files picker (up) |
| `f/` | `FquickWorkspacePicker` | Open workspace files picker |

---

## Plugin: REPL

Requires `peovim.plugins.repl`.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>rl` | `ReplSendLine` | Send current line to REPL |
| `<leader>rb` | `ReplSendBlock` | Send current block to REPL |

---

## Plugin: Local History

Requires `peovim.plugins.local_history`. No default key bindings are registered; wire them manually:

```python
keymap.nmap("<leader>ph", "<Plug>LocalHistory",       desc="Local history sidebar")
keymap.nmap("<leader>pH", "<Plug>LocalHistoryPicker", desc="Local history picker")
```

| `<Plug>` | Action |
|---------|--------|
| `LocalHistory` | Toggle local history sidebar |
| `LocalHistoryPicker` | Open local history picker |

---

## Plugin: SVN Signs

Requires `peovim.plugins.svnsigns` and `svn` on `PATH`.

| Key | Action |
|-----|--------|
| `]h` | Next SVN hunk |
| `[h` | Previous SVN hunk |
| `<leader>ss` | Open SVN status panel |
| `<leader>sd` | Open side-by-side SVN diff view |
| `<leader>sp` | Preview current hunk in float |

### SVN status panel-local keys

| Key | Action |
|-----|--------|
| `<CR>` / `l` | Open diff for M/C entries, open file otherwise |
| `o` | Open file in buffer (no diff) |
| `R` | Refresh status |

---

## Plugin: File History

Requires `peovim.plugins.filehistory`. Also load `peovim.plugins.local_history` to capture snapshots.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>fh` | `FileHistoryToggle` | Toggle file history sidebar |

### File history panel-local keys

| Key | Action |
|-----|--------|
| `j` / `k` | Navigate entries (preview updates live) |
| `r` / `R` | Refresh history |
| `q` | Close preview and hide sidebar |

---

## Plugin: Performance Panel

Requires `peovim.plugins.perf_panel`. No default keybinding; recommend:

```python
keymap.nmap("<leader>tp", lambda: api.ui.show_bottom_tab("perf"), desc="Perf panel")
```

Or open the bottom panel (`<A-p>`) and switch to the **perf** tab with `>` / `<`.

---

## Plugin: Proposed Edit Review

Requires `peovim.plugins.proposed_review`. Keys are active while a review is open.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>ra` | `ProposedReviewConfirm` | Apply the proposed edit |
| `<leader>rq` | `ProposedReviewCancel` | Discard the proposed edit |
| `<leader>rf` | `ProposedReviewFiles` | Choose file (multi-file edits) |
| `]r` | `ProposedReviewNext` | Next change block |
| `[r` | `ProposedReviewPrev` | Previous change block |

---

## Plugin: Session Additions

Requires `peovim.plugins.session_additions`. No keybindings. Configured via options:

```python
options.set("session_additions_enabled", True)
options.set("session_additions_sign_char", "+")
options.set("session_additions_sign_color", "80,200,80")
```

---

## Plugin: Todo List

Requires `peovim.plugins.todo`.

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>ct` | `TodoList` | Show TODO/FIXME list |

The plugin default binding is `<leader>xt`; remapped here:
```python
keymap.nunmap("<leader>xt")
keymap.nmap("<leader>ct", "<Plug>TodoList", desc="Todo list")
```

---

## Plugin: Copilot

Requires `peovim.plugins.copilot` and the copilot language server. Keys are insert-mode only.

| Key | Call | Mode | Action |
|-----|------|------|--------|
| `<C-y>` | `copilot.accept` | Insert | Accept Copilot suggestion |
| `<A-]>` | `copilot.cycle_next` | Insert | Next suggestion |
| `<A-[>` | `copilot.cycle_prev` | Insert | Previous suggestion |

Default in docs uses `<A-Tab>` for accept; remapped here to `<C-y>`:
```python
keymap.imap("<C-y>", copilot.accept, desc="Accept Copilot suggestion")
keymap.imap("<A-]>", copilot.cycle_next, desc="Next suggestion")
keymap.imap("<A-[>", copilot.cycle_prev, desc="Prev suggestion")
# Manual trigger when auto_trigger=False:
# keymap.imap("<A-Space>", copilot.trigger, desc="Request Copilot suggestion")
```

Configuration:
```python
copilot.debounce_ms     = 350   # ms after keystroke before requesting
copilot.max_ghost_lines = 3     # suggestion lines to display
copilot.auto_trigger    = True  # False = manual trigger only
```

---

## Plugin: Verilog LSP

Requires `peovim.plugins.verilog_lsp`. All bindings use a filetype guard and only fire in `.v`/`.sv` files.

| Key | Call / `<Plug>` | Mode | Action |
|-----|----------------|------|--------|
| `<leader>rh` | `vl.toggle_hierarchy(api)` | Normal | Toggle hierarchy panel |
| `<leader>pv` | `vl.toggle_hierarchy(api)` | Normal | Toggle hierarchy panel (panel group alias) |
| `<leader>rt` | `vl.trace_signal(api)` | Normal | Trace signal under cursor (picker) |
| `<leader>rr` | `vl.reparse(api)` | Normal | Force full workspace re-parse |
| `<leader>ru` | `_preview_pull_up_selection` | Normal, Visual | Preview hier-up for current line/selection |
| `<leader>rw` | `_prompt_push_down_range` | Normal, Visual | Preview hier-down (prompts for target module) |

### Hierarchy panel-local keys

| Key | Action |
|-----|--------|
| `i` | Jump to highlighted instance |
| `d` | Jump to highlighted module definition |
| `s` | Mark node as refactor source |
| `t` | Mark node as refactor destination |
| `c` | Preview hier-up for highlighted instance |
| `w` | Preview hier-down into target submodule |
| `g` | Open wrapper-candidate picker |
| `p` | Pin highlighted module as top |
| `P` | Clear top module pin |
| `<Esc>` | Close preview float and clear source/destination marks |

See [§Plugin: Proposed Edit Review](#plugin-proposed-edit-review) for review keys.

---

## Ex Commands

| Command | Description |
|---------|-------------|
| `:w` | Save |
| `:q` | Quit |
| `:wq` | Save and quit |
| `:w!` | Force save (overwrite externally changed file) |
| `:e <file>` | Open file |
| `:e` | Reload current file (if clean) |
| `:e!` | Reload and discard unsaved changes |
| `:checktime` (`:che`) | Check all open buffers for external changes now |
| `:bd` | Close buffer (return to alternate) |
| `:only` | Close all other windows |
| `:tabnew` | Open a new tab |
| `:tabnext` / `:tabn` | Go to next tab |
| `:tabprev` / `:tabp` | Go to previous tab |
| `:tabclose` | Close current tab |
| `:noh` | Clear search highlight |
| `:set <opt> <val>` | Set an option |
| `:format` | Format buffer |
| `:palette [theme]` | Open scratch color palette view |
| `:AlignChar <char>` | Align selected lines on a character |
| `:AlignRegex <pattern>` | Align selected lines on a regex |
| `:History` | Show local history for current file |
| `:HistoryOpen [index]` | Open a local history snapshot as read-only |
| `:HistoryRestore [index]` | Restore a local history snapshot into current buffer |
| `:HistoryPrune` | Prune old local history entries |
| `:Session` | Save session |
| `:SessionLoad` | Load session |
| `:SessionList` | List saved sessions |
| `:SessionDelete` | Delete a saved session |
| `:RecoverFile` | Recover autosaved content for current file |
| `:LspInfo` | Show LSP server status |
| `:LspRestart` | Restart LSP server |
| `:checkhealth` | Run health checks |
| `:Outline` | Toggle document outline sidebar |
| `:ReferencesPanel` | Toggle references sidebar |
| `:DiagnosticsPanel` | Toggle diagnostics sidebar |
| `:WorkspaceSymbolsPanel [query]` | Toggle workspace symbols sidebar |
| `:gitpanel` | Toggle git panel |
| `:GitBranchCreate <name>` | Create and check out new branch |
| `:GitCheckout <branch>` | Check out branch or ref |
| `:GitMergeBranch <name>` | Merge named branch into current |
| `:GitFetch [remote]` | Fetch and refresh sync state |
| `:GitPull [remote] [branch]` | Pull from upstream |
| `:GitPush [remote] [branch]` | Push to upstream |
| `:GitCommit <message>` | Create a git commit with the given message |
| `:GitLog [ref]` | Open scratch git log browser |
| `:GitDiffFile <path>` | Diff git status file against HEAD |
| `:GitStageFile <path>` | Stage file |
| `:GitUnstageFile <path>` | Unstage file |
| `:GitDiscardFile <path>` | Discard file changes |
| `:ExplorerCreate <path>` | Create file or directory |
| `:ExplorerRename <name>` | Rename selected explorer entry |
| `:ExplorerDelete` | Delete selected explorer entry |
| `:Codemap` | Refresh and open codemap sidebar |
| `:CopilotAuth` | Re-run Copilot device-flow authentication |
| `:CopilotStatus` | Show Copilot auth status |
| `:VerilogHierUp` | Preview hier-up for current line/selection |
| `:VerilogHierDownRange <module> [instance]` | Preview hier-down |
| `:VerilogPushDownRange <module> [instance]` | Preview range-based hier push-down |
| `:DiffDebug` | Print current diff session state |
| `:LogOn [modules=...] [level=...]` | Enable logging |
| `:LogView` | Open log output panel |

`<Tab>` on the command line opens a fuzzy-filtered command picker. `<Up>`/`<Down>` selects; `<Tab>` again inserts.
