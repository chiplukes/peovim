# Vim Compatibility Reference

Target: comprehensive coverage of vi/vim/neovim commands out of the box.

## Status Overview

Legend: **✓ implemented** | **~ partial** | **✗ not implemented**

| Section | Status | Notes |
|---|---|---|
| Normal mode motions (char/word/line/file/scroll) | ✓ | |
| Screen-position motions (`H`/`M`/`L`) | ✗ | not yet bound |
| Find-char motions (`f`/`t`/`F`/`T`/`;`/`,`) | ✓ | |
| Matching (`%`) | ✓ | |
| Paragraph (`{`/`}`) / sentence (`(`/`)`) | ✓ | |
| Search (`/`/`?`/`n`/`N`/`*`/`#`) | ✓ | |
| Marks (`m`/`'`/`` ` ``) | ✓ | |
| Operators (`d`,`c`,`y`,`>`,`<`,`=`,`!`,`~`) | ~ | `~` operator works via `g~`; single-char `~` not bound |
| Text objects | ✓ | |
| Insert / change / delete commands (`i`,`a`,`I`,`A`,`o`,`O`,`x`,`X`,`s`) | ✓ | `S`,`C`,`D` not bound |
| Paste (`p`/`P`) | ✓ | `gp`/`gP`, `]p` not yet bound |
| Case toggle (`g~`/`gu`/`gU`) / `Ctrl-A` / `Ctrl-X` | ✓ | single-char `~` not bound |
| Join lines (`J`) | ✗ | action implemented, no key binding |
| Undo / redo | ✓ | `U` (undo line) not bound |
| Repeat (`.`) / dot-repeat | ✓ | |
| Registers (`"`/`0`-`9`/`a`-`z`/`*`/`+`/`_`) | ✓ | |
| Macros (`q`/`@`) | ✓ | |
| Window commands (`Ctrl-w`) | ✓ | |
| Tab pages (`gt`/`gT`/`:tabnew`/`:tabclose`) | ✓ | |
| Folding | ~ | `zf`,`zo`,`zc`,`za`,`zR`,`zM`,`zd`,`zz`,`zt`,`zb` done; `zA` not |
| Insert mode — core | ✓ | |
| Insert mode — advanced (`Ctrl-o`, completion, digraphs) | ✗ | |
| Visual mode — core | ✓ | |
| Visual mode — `=`, `J`, filter | ✗ | |
| Command-line (`:` ex commands — file, buffer, window, edit) | ~ | many Vim ex commands not registered (see details) |
| `:set` options | ~ | generic pass-through; not all options affect rendering (see Options section) |
| `g` commands | ~ | `gg`,`gv`,`ge`,`gE`,`gI`,`gd`,`gf`,`gt`,`gT`,`g~`,`gu`,`gU`,`g*`,`g#` done; `gj`,`gk`,`g0`,`g$`,`gD`,`gq`,`gw`,`g;`,`g,`,`gn`,`gN`,`gp`,`gP` not |
| `z` scroll/fold commands | ~ | zz/zt/zb/zf/zo/zc/za/zR/zM/zd done; `z.` not |

---

## Normal Mode Motions

### Character
| Key | Action |
|---|---|
| `h` / `←` | left |
| `l` / `→` | right |
| `j` / `↓` | down (virtual col preserved) |
| `k` / `↑` | up (virtual col preserved) |

### Word
| Key | Action |
|---|---|
| `w` / `W` | next word start (word / WORD) |
| `b` / `B` | prev word start |
| `e` / `E` | next word end |
| `ge` / `gE` | prev word end |

### Line
| Key | Action |
|---|---|
| `0` | start of line (col 0) |
| `^` | first non-blank |
| `$` | end of line |
| `+` / `Enter` | first non-blank of next line |
| `-` | first non-blank of prev line |
| `g_` | last non-blank _(not implemented)_ |
| `\|` | column N _(not implemented)_ |

### File
| Key | Action |
|---|---|
| `gg` | first line |
| `G` | last line |
| `{N}G` | line N |
| `{N}%` | N percent through file |

### Screen
| Key | Action |
|---|---|
| `H` | top of screen _(not implemented)_ |
| `M` | middle of screen _(not implemented)_ |
| `L` | bottom of screen _(not implemented)_ |

### Scroll
| Key | Action |
|---|---|
| `Ctrl-d` | half page down |
| `Ctrl-u` | half page up |
| `Ctrl-f` | full page down |
| `Ctrl-b` | full page up |
| `Ctrl-e` | scroll down 1 line (cursor stays) |
| `Ctrl-y` | scroll up 1 line |
| `zz` | center cursor line |
| `zt` | cursor to top |
| `zb` | cursor to bottom |
| `z.` | center + first non-blank _(not implemented)_ |

### Find character on line
| Key | Action |
|---|---|
| `f{c}` | next occurrence of char c |
| `F{c}` | prev occurrence |
| `t{c}` | one before next occurrence |
| `T{c}` | one after prev occurrence |
| `;` | repeat last f/F/t/T |
| `,` | repeat reverse |

### Matching
| Key | Action |
|---|---|
| `%` | jump to matching bracket/paren/brace |
| `[(` `])` | prev/next unmatched `(` `)` |
| `[{` `]}` | prev/next unmatched `{` `}` |
| `[[` `]]` | prev/next section (`{` in col 0) |
| `[]` `][` | prev/next end of section |

### Paragraph / sentence
| Key | Action |
|---|---|
| `{` | prev empty line (paragraph) |
| `}` | next empty line |
| `(` | prev sentence |
| `)` | next sentence |

### Search
| Key | Action |
|---|---|
| `/{pat}` | search forward |
| `?{pat}` | search backward |
| `n` | next match |
| `N` | prev match |
| `*` | forward search word under cursor |
| `#` | backward search word under cursor |
| `g*` `g#` | like `*`/`#` but no word boundaries |
| `gd` | go to local declaration (text search) |

### Marks / jumps
| Key | Action |
|---|---|
| `m{a-zA-Z}` | set mark |
| `` `{a-zA-Z} `` | jump to mark (exact position) |
| `'{a-zA-Z}` | jump to mark (first non-blank) |
| `` `. `` | jump to last change |
| `` `[ `` `` `] `` | start/end of last change or yank |
| `` `< `` `` `> `` | start/end of last visual selection |
| `Ctrl-o` | older jump list position |
| `Ctrl-i` / `Tab` | newer jump list position |

### Misc navigation
| Key | Action |
|---|---|
| `gf` | go to file under cursor |
| `gj` / `gk` | down/up by screen line _(not implemented)_ |
| `g0` / `g$` | start/end of screen line _(not implemented)_ |
| `g;` / `g,` | older/newer change list position _(not implemented)_ |

---

## Operators (take a motion or text object)

| Operator | Action |
|---|---|
| `d` | delete (into register) |
| `y` | yank |
| `c` | change (delete + enter Insert) |
| `>` | indent right |
| `<` | indent left |
| `=` | auto-indent (reformat) |
| `g~` | toggle case |
| `gu` | lowercase |
| `gU` | uppercase |
| `!` | filter through external command |
| `~` | toggle case (single char in Normal) _(not bound — use `g~{motion}`)_ |
| `gq` | format (wrap lines) _(not implemented)_ |

**Operator shorthand (doubled = whole line):**
`dd`, `yy`, `cc`, `>>`, `<<`, `==`, `g~~` (guu`, `gUU` work)

---

## Text Objects (used after operator or in Visual)

### Word/WORD
`iw` `aw` `iW` `aW`

### Quoted strings
`i"` `a"` `i'` `a'` `` i` `` `` a` ``

### Brackets
`i(` `a(` `ib` `ab`  (parentheses)
`i{` `a{` `iB` `aB`  (braces)
`i[` `a[`             (brackets)
`i<` `a<`             (angle brackets)

### Blocks
`ip` `ap`  paragraph
`is` `as`  sentence
`it` `at`  HTML/XML tag

---

## Normal Mode — Special Commands

### Inserting
| Key | Action |
|---|---|
| `i` | insert before cursor |
| `I` | insert at first non-blank |
| `a` | append after cursor |
| `A` | append at end of line |
| `o` | open new line below |
| `O` | open new line above |
| `s` | substitute char (= `cl`) |
| `S` | substitute line _(not bound — use `cc`)_ |
| `C` | change to end of line _(not bound — use `c$`)_ |
| `R` | replace mode |
| `gI` | insert at col 1 |

### Deleting
| Key | Action |
|---|---|
| `x` | delete char under cursor |
| `X` | delete char before cursor |
| `D` | delete to end of line _(not bound — use `d$`)_ |

### Paste
| Key | Action |
|---|---|
| `p` | paste after cursor / below line |
| `P` | paste before cursor / above line |
| `gp` `gP` | like `p`/`P` but cursor after pasted text _(not implemented)_ |
| `]p` | paste with adjusted indent _(not implemented)_ |

### Case / misc
| Key | Action |
|---|---|
| `~` | toggle case of char, advance _(not bound — use `g~l`)_ |
| `J` | join lines _(not yet bound)_ |
| `gJ` | join lines without space _(not implemented)_ |
| `Ctrl-a` | increment number under cursor |
| `Ctrl-x` | decrement number |

### Undo / redo
| Key | Action |
|---|---|
| `u` | undo |
| `Ctrl-r` | redo |
| `U` | undo all changes on line _(not implemented)_ |

### Repeat
| Key | Action |
|---|---|
| `.` | repeat last change |
| `@:` | repeat last ex command |

### Registers
| Key | Action |
|---|---|
| `"{reg}` | use register for next d/y/c/p |
| `"*y` `"*p` | yank/paste clipboard (X11/Wayland) |
| `"+y` `"+p` | yank/paste system clipboard |

### Macros
| Key | Action |
|---|---|
| `q{a-z}` | record macro into register |
| `q` | stop recording |
| `@{a-z}` | play macro |
| `@@` | repeat last macro |
| `{N}@{reg}` | play macro N times |

### Window commands (`Ctrl-w` prefix)
| Key | Action |
|---|---|
| `Ctrl-w s` / `:split` | horizontal split |
| `Ctrl-w v` / `:vsplit` | vertical split |
| `Ctrl-w h/j/k/l` | move focus |
| `Ctrl-w H/J/K/L` | move window to edge _(not implemented)_ |
| `Ctrl-w w` | cycle focus |
| `Ctrl-w p` | alternate window _(not implemented)_ |
| `Ctrl-w =` | equalize window sizes |
| `Ctrl-w +` / `Ctrl-w -` | increase/decrease height |
| `Ctrl-w >` / `Ctrl-w <` | increase/decrease width |
| `Ctrl-w _` | maximize height _(not implemented)_ |
| `Ctrl-w \|` | maximize width _(not implemented)_ |
| `Ctrl-w c` / `Ctrl-w q` | close window |
| `Ctrl-w o` | close all other windows |
| `Ctrl-w n` | new window _(not implemented)_ |
| `Ctrl-w r` / `Ctrl-w R` | rotate windows _(not implemented)_ |
| `Ctrl-w x` | exchange with next _(not implemented)_ |

### Tab page commands
| Key / Command | Action |
|---|---|
| `gt` / `:tabnext` | next tab |
| `gT` / `:tabprev` | prev tab |
| `{N}gt` | go to tab N |
| `:tabnew` | new tab |
| `:tabclose` | close tab |
| `:tabonly` | close other tabs _(not implemented)_ |
| `:tabmove {N}` | move tab _(not implemented)_ |

### Folding

_Status: ~ partial — `zf`, `zo`/`zO`, `zc`, `za`, `zR`, `zM`, `zd` implemented; `zA` (recursive toggle) not implemented._

| Key | Action |
|---|---|
| `zf{motion}` | create fold |
| `zo` / `zO` | open fold |
| `zc` | close fold |
| `za` | toggle fold |
| `zA` | toggle fold recursively _(not implemented)_ |
| `zR` | open all folds |
| `zM` | close all folds |
| `zd` | delete fold |

---

## Insert Mode

| Key | Action |
|---|---|
| `Esc` / `Ctrl-[` / `Ctrl-c` | back to Normal |
| `Ctrl-h` / `BS` | delete char back |
| `Ctrl-w` | delete word back |
| `Ctrl-u` | delete to start of line |
| `Ctrl-r{reg}` | insert register contents |
| `Ctrl-r Ctrl-r{reg}` | insert literally _(not implemented)_ |
| `Ctrl-r =` | insert expression result _(not implemented — no expression eval)_ |
| `Ctrl-o{cmd}` | execute one Normal command _(not implemented)_ |
| `Ctrl-t` | indent line |
| `Ctrl-d` | de-indent line |
| `Ctrl-n` / `Ctrl-p` | keyword completion _(not implemented; use LSP popup)_ |
| `Ctrl-x Ctrl-f` | filename completion _(not implemented)_ |
| `Ctrl-x Ctrl-l` | whole-line completion _(not implemented)_ |
| `Ctrl-a` | insert previously inserted text _(not implemented)_ |
| `Ctrl-e` | insert char below cursor _(not implemented)_ |
| `Ctrl-y` | insert char above cursor _(not implemented)_ |
| `Ctrl-v{key}` | insert key literally _(not implemented)_ |
| `Ctrl-v{NNN}` | insert by decimal char code _(not implemented)_ |
| `Ctrl-k{d1}{d2}` | insert digraph _(not implemented)_ |
| `BS` / `Del` | delete |
| `Enter` | newline with autoindent |
| `Tab` | insert tab / expand depending on options |
| `Shift-Tab` | de-indent |

---

## Visual Mode

| Key | Action |
|---|---|
| `v` | enter visual character |
| `V` | enter visual line |
| `Ctrl-v` | enter visual block |
| `o` | move to other end of selection |
| `O` | move to other corner (block mode only) |
| `gv` | reselect last visual selection |
| `Esc` | back to Normal |
| (all motions) | extend selection |
| `d` / `c` / `y` | delete / change / yank selection |
| `r{c}` | replace all chars in selection |
| `I` | insert at start of each block line |
| `A` | append at end of each block line |
| `p` / `P` | paste (block mode only) _(char/line visual paste not implemented)_ |
| `J` | join selected lines _(not implemented)_ |
| `u` / `U` | lower/uppercase selection |
| `~` | toggle case of selection |
| `>` / `<` | indent/dedent |
| `=` | auto-indent _(not implemented)_ |
| `!{cmd}` | filter selection through cmd _(not implemented)_ |

---

## Command Mode (`:`)

### File / Buffer
| Command | Implemented | Notes |
|---|---|---|
| `:w` `:w!` | ✓ | |
| `:w {file}` | ✓ | |
| `:wa` `:wa!` | ✗ | |
| `:q` `:q!` | ✓ | |
| `:qa` `:qa!` | ✗ | |
| `:wq` `:x` | ✓ | |
| `:ZZ` `:ZQ` | ✓ | normal-mode key sequences, not ex commands |
| `:e {file}` | ✓ | |
| `:e!` | ✓ | |
| `:enew` | ✗ | |
| `:r {file}` | ✗ | |
| `:r !{cmd}` | ✗ | |
| `:f` `:file` | ✗ | |

### Buffers
| Command | Implemented | Notes |
|---|---|---|
| `:ls` `:buffers` `:files` | ✗ | |
| `:b {N\|name}` | ✗ | |
| `:bn` `:bp` | ✗ | |
| `:bd` `:bd!` | ✓ | registered as `bdelete` |
| `:bwipe` | ✗ | |
| `:ball` | ✗ | |
| `Ctrl-^` | ✓ | toggle alternate file |

### Windows / Splits
| Command | Implemented | Notes |
|---|---|---|
| `:split` `:sp` | ✓ | |
| `:vsplit` `:vs` | ✓ | |
| `:new` | ✗ | |
| `:vnew` | ✗ | |
| `:close` | ✓ | |
| `:only` | ✓ | |
| `:resize {N}` | ✗ | |
| `:vertical resize {N}` | ✗ | |
| `:wincmd {key}` | ✗ | |

### Tabs
| Command | Implemented |
|---|---|
| `:tabnew` | ✓ |
| `:tabnext` / `:tabn` | ✓ |
| `:tabprev` / `:tabp` | ✓ |
| `:tabclose` | ✓ |
| `:tabonly` | ✗ |
| `:tabmove` | ✗ |

### Navigation
| Command | Action |
|---|---|
| `:{N}` | go to line N |
| `:+{N}` `:-{N}` | relative line |
| `:/pat/` | jump to pattern |

### Editing
| Command | Implemented | Notes |
|---|---|---|
| `:{range}d {reg}` | ✓ | |
| `:{range}y {reg}` | ✓ | |
| `:{range}s/{pat}/{rep}/{flags}` | ✓ | `g`, `i`, `c` flags |
| `:%s/...` | ✓ | |
| `:'<,'>s/...` | ✓ | |
| `:{range}m {addr}` | ✗ | move lines |
| `:{range}co {addr}` / `:t` | ✗ | copy lines |
| `:{range}j` | ✗ | join lines |
| `:{range}g/{pat}/{cmd}` | ✗ | global command |
| `:{range}v/{pat}/{cmd}` | ✗ | inverted global |
| `:{range}sort` | ✗ | |
| `:{range}!{cmd}` | ✓ | filter through shell |

### Settings
| Command | Implemented |
|---|---|
| `:set {option}` / `:set no{option}` / `:set {option}!` | ✓ |
| `:set {option}={val}` / `:set {option}?` | ✓ |
| `:setlocal` | ✗ |
| `:setglobal` | ✗ |

### Keymaps
| Command | Implemented |
|---|---|
| `:nmap` / `:nnoremap` | ✓ |
| `:imap` / `:inoremap` | ✓ |
| `:vmap` / `:vnoremap` | ✓ |
| `:unmap` / `:nunmap` | ✓ |
| `:omap` / `:onoremap` | ✗ |
| `:cmap` / `:cnoremap` | ✗ |
| `:map` (list all) | ✗ |

### Misc
| Command | Implemented | Notes |
|---|---|---|
| `:!{cmd}` | ✓ | run shell command |
| `:!!` | ✗ | repeat last shell command |
| `:pwd` | ✗ | |
| `:cd {dir}` | ✗ | |
| `:normal {cmds}` | ✓ | |
| `:echo {expr}` | ✓ | |
| `:colorscheme {name}` | ✓ | |
| `:syntax on\|off` | ✗ | |
| `:filetype {name}` | ✗ | |
| `:messages` | ✓ | |
| `:nohlsearch` / `:noh` | ✓ | |
| `:checkhealth` | ✓ | |
| `:help` | ✗ | |
| `:version` | ✗ | |
| `:let {var} = {expr}` | ✗ | |
| `:source {file}` | ✗ | |

---

## Options (`:set`)

_Status: ~ partial — `:set key=val` / `:set nokey` stores any option on the window
options dict (generic pass-through). Options that actively affect rendering or
behaviour are marked ✓ below. Unmarked rows are accepted silently but may have no
effect yet. Defaults shown are **peovim defaults**, which differ from Vim's in
several cases (e.g. `expandtab`, `tabstop`, `autoindent`, `hlsearch`, `scrolloff`)._

### Display
| Option | Type | Default | Status |
|---|---|---|---|
| `number` / `nu` | bool | false | ✓ |
| `relativenumber` / `rnu` | bool | false | ✓ |
| `wrap` | bool | true | ✓ |
| `linebreak` / `lbr` | bool | false | ~ |
| `cursorline` / `cul` | bool | false | ✓ |
| `cursorcolumn` / `cuc` | bool | false | ~ |
| `list` | bool | false | ~ |
| `listchars` / `lcs` | string | | ~ |
| `scrolloff` / `so` | int | 4 | ✓ |
| `sidescrolloff` / `siso` | int | 0 | ~ |
| `signcolumn` / `scl` | string | auto | ✓ |
| `colorcolumn` / `cc` | string | | ✓ |
| `indentguides` | string | none | ✓ (peovim extension) |

### Editing
| Option | Type | Default | Status |
|---|---|---|---|
| `expandtab` / `et` | bool | true | ✓ |
| `tabstop` / `ts` | int | 4 | ✓ |
| `shiftwidth` / `sw` | int | 4 | ✓ |
| `softtabstop` / `sts` | int | 0 | ~ |
| `autoindent` / `ai` | bool | true | ✓ |
| `smartindent` / `si` | bool | false | ~ |
| `textwidth` / `tw` | int | 0 | ~ |
| `fileformat` / `ff` | string | unix | ✓ |
| `fileencoding` / `fenc` | string | | ✓ |

### Search
| Option | Type | Default | Status |
|---|---|---|---|
| `hlsearch` / `hls` | bool | true | ✓ |
| `ignorecase` / `ic` | bool | false | ✓ |
| `smartcase` / `scs` | bool | false | ✓ |
| `wrapscan` / `ws` | bool | true | ✓ |
| `incsearch` / `is` | bool | false | ~ |

### Behavior
| Option | Type | Default | Status |
|---|---|---|---|
| `clipboard` / `cb` | string | | ✓ (`unnamed`/`unnamedplus`) |
| `mouse` | string | | ~ |
| `undolevels` / `ul` | int | 1000 | ~ |

---

## `g` Commands

_Status: ~ partial — implemented: `gg`, `ge`/`gE`, `gv`, `gI`, `gd`, `gf`, `gt`/`gT`, `g~`, `gu`, `gU`, `g*`, `g#`. Not implemented: `gD`, `gj`/`gk`, `g0`/`g$`/`g^`, `gm`, `gq`, `gw`, `g;`/`g,`, `gn`/`gN`, `gp`/`gP`._

| Key | Action | Status |
|---|---|---|
| `gg` | go to first line | ✓ |
| `ge` / `gE` | prev word end | ✓ |
| `gv` | reselect last visual | ✓ |
| `gI` | insert at col 1 | ✓ |
| `gd` | go to local declaration | ✓ (text search) |
| `gf` | go to file under cursor | ✓ |
| `gt` / `gT` | next / prev tab page | ✓ |
| `g~{motion}` | toggle case | ✓ |
| `gu{motion}` | lowercase | ✓ |
| `gU{motion}` | uppercase | ✓ |
| `g*` / `g#` | search word (no `\b`) | ✓ |
| `gD` | go to global declaration | ✗ |
| `gj` / `gk` | screen line down/up | ✗ |
| `g0` / `g$` / `g^` | screen line start/end | ✗ |
| `gm` | go to middle of screen line | ✗ |
| `gq{motion}` | format / wrap lines | ✗ |
| `gw{motion}` | format, keep cursor | ✗ |
| `g;` / `g,` | change list older/newer | ✗ |
| `gn` / `gN` | search + visual select match | ✗ |
| `gp` / `gP` | paste, cursor after | ✗ |

---

## Registers Reference

| Register | Name | Notes |
|---|---|---|
| `"` | unnamed | Last d/c/s/x/y goes here |
| `0` | yank | Last `y` command |
| `1`-`9` | delete history | Shifted on each delete |
| `-` | small delete | Deletes < 1 line |
| `a`-`z` | named | User-managed |
| `A`-`Z` | named append | Appends to lowercase |
| `*` | primary selection | X11 primary / Windows clipboard |
| `+` | clipboard | System clipboard |
| `_` | black hole | Discard |
| `.` | last insert | Last inserted text (read-only) |
| `:` | last command | Last ex command (read-only) |
| `%` | filename | Current file (read-only) |
| `#` | alt filename | Alternate file (read-only) |
| `/` | last search | Last search pattern |
| `=` | expression | _(read-only expression paste not implemented)_ |

---

## Special Characters / Notation

In keymaps and command output:
- `<CR>` = Enter
- `<Esc>` = Escape
- `<Space>` = Space
- `<Tab>` = Tab
- `<BS>` = Backspace
- `<Del>` = Delete
- `<Left>` `<Right>` `<Up>` `<Down>` = arrows
- `<F1>`..`<F12>` = function keys
- `<C-x>` = Ctrl+x
- `<M-x>` / `<A-x>` = Meta/Alt+x
- `<S-x>` = Shift+x
- `<leader>` = value of `leader` option (default `\`)

---

## Vim Features Explicitly Out of Scope

These are Vim features we will NOT implement:
- `vimscript` / `vimL` scripting language (replaced by Python plugins)
- `cscope` integration
- `netrw` (built-in file browser) — will have a better picker
- GUI variants (gvim) — terminal only
- `client-server` mode
- `matchit` plugin — `%` extended matching built into syntax engine
