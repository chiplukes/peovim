"""
Gutter signs for git hunks; hunk preview, stage, and reset.

Implemented against the public peovim.api — no internal imports.
See notes/plugins.md for plugin development.

Signs:
  │ green  — added lines
  │ yellow — changed lines
  ▁ red    — deleted lines (shown at adjacent line)

Usage in init.py:
    plugins.load('peovim.plugins.gitsigns')

    # Optional: echo git commands as notifications
    api.git.verbose = True
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from peovim.core.persistence import atomic_write_text
from peovim.git import color_for_status_entry
from peovim.plugins import vcssigns as _vcs

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI
    from peovim.ui.tree_view import TreeNode

_NAMESPACE = "gitsigns"

# sign_type_name → (char, fg_color)
_SIGN_DEFS: dict[str, tuple[str, tuple[int, int, int]]] = {
    "gitsigns.add": ("│", (80, 200, 80)),  # green
    "gitsigns.change": ("│", (200, 200, 80)),  # yellow
    "gitsigns.delete": ("▁", (200, 80, 80)),  # red
}

_HUNK_TYPE_TO_SIGN = {
    "add": "gitsigns.add",
    "change": "gitsigns.change",
    "delete": "gitsigns.delete",
}

# Module-level debounce state (buf_id → asyncio.TimerHandle)
_debounce_timers: dict[int, Any] = {}
_tokens: list[int] = []
_status_panel: _GitStatusSidebarPanel | None = None


class _GitStatusSidebarPanel:
    def __init__(self, api: Any, *, width: int = 30) -> None:
        from peovim.ui.tree_view import TreeView

        self._api = api
        self.width = width
        self._tree = TreeView([], title="Git", on_select=self._on_select, on_key=self._on_key, width=width)

    def render(self, grid: Any) -> None:
        self._tree.focused = getattr(self, "_sidebar_focused", False)
        self._tree.blink_on = getattr(self, "_sidebar_blink_on", True)
        self._tree._width = grid.width
        title = self._tree._title
        self._tree._title = ""
        try:
            self._tree.render(grid)
        finally:
            self._tree._title = title

    def feed_key(self, key: str) -> bool:
        if key == "R":
            self.refresh()
            return True
        self._tree.feed_key(key)
        return True

    def on_focus(self) -> None:
        self._tree.focused = True

    def on_blur(self) -> None:
        self._tree.focused = False

    def on_show(self) -> None:
        self.refresh()
        self._api.ui.notify("Git panel: press ? for help", level="info")

    def refresh(self) -> None:
        title, nodes = _build_status_nodes(self._api)
        self._tree._title = title
        self._tree.set_nodes(nodes)

    def _on_select(self, node: Any) -> None:
        status_path = _selected_status_path(node)
        if status_path is None:
            return
        path = Path(status_path)
        if path.is_file():
            self._api.open_buffer(path)
            self._api.ui.blur_sidebar()

    def _on_key(self, key: str, node: Any) -> bool:
        branch_ref = _selected_branch_ref(node)
        checkout_branch = _selected_checkout_branch_name(node)
        status_path = _selected_status_path(node)
        if key == "?":
            _show_panel_help(self._api)
            return True
        if key == "C":
            self._api.open_cmdline("GitCommit ")
            return True
        if key == "c":
            self._api.open_cmdline("GitBranchCreate ")
            return True
        if key == "f":
            self._api.open_cmdline(_fetch_cmdline(self._api))
            return True
        if key == "p":
            self._api.open_cmdline(_pull_cmdline(self._api))
            return True
        if key == "P":
            self._api.open_cmdline(_push_cmdline(self._api))
            return True
        if key == "a" and status_path:
            _cmd_stage_file(self._api, status_path)
            return True
        if key == "u" and status_path:
            _cmd_unstage_file(self._api, status_path)
            return True
        if key == "x" and status_path:
            _cmd_discard_file(self._api, status_path)
            return True
        if key == "d" and status_path:
            if _cmd_compare_file(self._api, status_path):
                self._api.ui.blur_sidebar()
            return True
        if key == "l":
            if _cmd_log(self._api, branch_ref or ""):
                self._api.ui.blur_sidebar()
            return True
        if key == "s" and checkout_branch:
            self._api.open_cmdline(f"GitCheckout {checkout_branch}")
            return True
        if key == "m" and branch_ref:
            self._api.open_cmdline(f"GitMergeBranch {branch_ref}")
            return True
        return False


def setup(api: EditorAPI) -> None:  # cm:7e8b5d
    """Register gitsigns plugin with the editor."""
    global _tokens, _status_panel
    _tokens = []

    # Register sign types
    _vcs.register_sign_defs(api, _SIGN_DEFS)

    # Subscribe to buffer events
    tok1 = api.events.on("buffer_opened", lambda **kw: _on_immediate(api, **kw))
    tok2 = api.events.on("buffer_saved", lambda **kw: _on_immediate(api, **kw))
    tok3 = api.events.on("buffer_changed", lambda **kw: _on_debounced(api, **kw))
    _tokens.extend([tok1, tok2, tok3])

    # Ex commands
    api.commands.register("githunkpreview", lambda cmd, ctx: _cmd_hunk_preview(api), min_abbrev=7)
    api.commands.register("gitstagunk", lambda cmd, ctx: _cmd_stub(api, "GitStageHunk"), min_abbrev=8)
    api.commands.register("gitresethunk", lambda cmd, ctx: _cmd_stub(api, "GitResetHunk"), min_abbrev=8)
    api.commands.register("GitBranchCreate", lambda cmd, ctx: _cmd_branch_create(api, cmd.args.strip()), min_abbrev=12)
    api.commands.register("GitCheckout", lambda cmd, ctx: _cmd_checkout(api, cmd.args.strip()), min_abbrev=8)
    api.commands.register("GitMergeBranch", lambda cmd, ctx: _cmd_merge_branch(api, cmd.args.strip()), min_abbrev=10)
    api.commands.register("GitFetch", lambda cmd, ctx: _cmd_fetch(api, cmd.args.strip()), min_abbrev=8)
    api.commands.register("GitPull", lambda cmd, ctx: _cmd_pull(api, cmd.args.strip()), min_abbrev=7)
    api.commands.register("GitPush", lambda cmd, ctx: _cmd_push(api, cmd.args.strip()), min_abbrev=7)
    api.commands.register("GitLog", lambda cmd, ctx: _cmd_log(api, cmd.args.strip()), min_abbrev=6)
    api.commands.register("GitCommit", lambda cmd, ctx: _cmd_commit(api, cmd.args.strip()), min_abbrev=9)
    api.commands.register("GitStageFile", lambda cmd, ctx: _cmd_stage_file(api, cmd.args.strip()), min_abbrev=10)
    api.commands.register("GitUnstageFile", lambda cmd, ctx: _cmd_unstage_file(api, cmd.args.strip()), min_abbrev=12)
    api.commands.register("GitDiscardFile", lambda cmd, ctx: _cmd_discard_file(api, cmd.args.strip()), min_abbrev=12)
    api.commands.register("GitCompareFile", lambda cmd, ctx: _cmd_compare_file(api, cmd.args.strip()), min_abbrev=11)
    api.commands.register("GitDiffFile", lambda cmd, ctx: _cmd_compare_file(api, cmd.args.strip()), min_abbrev=8)

    # Hunk navigation — also exposed as <Plug> for user remapping
    api.keymap.define_plug("GitsignsNextHunk", lambda: _next_hunk(api), desc="Git: next hunk")
    api.keymap.define_plug("GitsignsPrevHunk", lambda: _prev_hunk(api), desc="Git: previous hunk")
    api.keymap.define_plug("GitsignsStatusPanel", lambda: _toggle_status_panel(api), desc="Git: panel")
    api.keymap.nmap("]c", "<Plug>GitsignsNextHunk", desc="Git: next hunk")
    api.keymap.nmap("[c", "<Plug>GitsignsPrevHunk", desc="Git: previous hunk")
    api.keymap.nmap("<leader>gs", "<Plug>GitsignsStatusPanel", desc="Git: panel")

    api.commands.register("gitpanel", lambda cmd, ctx: _toggle_status_panel(api), min_abbrev=7)
    api.commands.register("gitstatuspanel", lambda cmd, ctx: _toggle_status_panel(api), min_abbrev=7)

    _status_panel = _GitStatusSidebarPanel(api)
    api.ui.register_sidebar_panel("git-status", _status_panel)

    # Scan already-open buffers
    for buf in api.list_buffers():
        _update_signs(api, buf)


def teardown() -> None:
    """Cancel pending debounce timers."""
    global _status_panel
    for handle in _debounce_timers.values():
        with contextlib.suppress(Exception):
            handle.cancel()
    _debounce_timers.clear()
    _status_panel = None


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


def _on_immediate(api: Any, **kwargs: Any) -> None:
    """buffer_opened / buffer_saved → update signs right away."""
    buf_id: int | None = kwargs.get("buf_id")
    if buf_id is None:
        return
    for buf in api.list_buffers():
        if buf.buf_id == buf_id:
            _update_signs(api, buf)
            if api.ui.is_sidebar_visible("git-status"):
                panel = api.ui.get_sidebar_panel("git-status")
                if panel is not None and hasattr(panel, "refresh"):
                    panel.refresh()
            return


def _on_debounced(api: Any, **kwargs: Any) -> None:
    """buffer_changed → debounced update (500 ms)."""
    buf_id: int | None = kwargs.get("buf_id")
    if buf_id is None:
        return

    # Cancel existing timer for this buffer
    old = _debounce_timers.pop(buf_id, None)
    if old is not None:
        with contextlib.suppress(Exception):
            old.cancel()

    # Find the buffer object
    target_buf = None
    for buf in api.list_buffers():
        if buf.buf_id == buf_id:
            target_buf = buf
            break
    if target_buf is None:
        return

    # Schedule update in 500 ms; fall back to immediate if no loop
    try:
        loop = asyncio.get_event_loop()
        handle = loop.call_later(0.5, lambda: _refresh_buffer_git_state(api, target_buf))
        _debounce_timers[buf_id] = handle
    except RuntimeError:
        _refresh_buffer_git_state(api, target_buf)


def _refresh_buffer_git_state(api: Any, buf: Any) -> None:
    _update_signs(api, buf)
    if api.ui.is_sidebar_visible("git-status"):
        panel = api.ui.get_sidebar_panel("git-status")
        if panel is not None and hasattr(panel, "refresh"):
            panel.refresh()


# ---------------------------------------------------------------------------
# Core: update signs for one buffer
# ---------------------------------------------------------------------------


def _update_signs(api: Any, buf: Any) -> None:
    """Clear gitsigns namespace and re-place signs from git hunks."""
    _vcs.update_signs(api, buf, _NAMESPACE, _HUNK_TYPE_TO_SIGN, api.git.get_hunks)


# ---------------------------------------------------------------------------
# Hunk navigation helpers
# ---------------------------------------------------------------------------


def _current_hunks(api: Any) -> list[dict]:
    """Return hunks for the active buffer."""
    return _vcs.current_hunks(api, api.git.get_hunks)


def _next_hunk(api: Any) -> None:
    """Move cursor to the start of the next git hunk."""
    _vcs.next_hunk(api, api.git.get_hunks)


def _prev_hunk(api: Any) -> None:
    """Move cursor to the start of the previous git hunk."""
    _vcs.prev_hunk(api, api.git.get_hunks)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_hunk_preview(api: Any) -> None:
    """Show a float with the diff for the hunk under the cursor."""
    try:
        win = api.active_window()
        cursor_line = win.cursor[0]
        hunks = _current_hunks(api)
        hunk = next(
            (h for h in hunks if h["start"] <= cursor_line <= h["end"]),
            None,
        )
        if hunk is None:
            api.ui.notify("No hunk at cursor", level="info")
            return
        lines = [
            f"  type:  {hunk['type']}",
            f"  lines: {hunk['start'] + 1}–{hunk['end'] + 1}",
        ]
        api.ui.open_float(lines, title="Hunk Preview", width=40, height=len(lines) + 2)
    except Exception:
        pass


def _cmd_stub(api: Any, name: str) -> None:
    api.ui.notify(f"{name}: not yet implemented", level="info")


def _cmd_commit(api: Any, message: str) -> None:
    if not message:
        api.ui.notify("GitCommit expects a commit message", level="info")
        return
    root = _git_root(api)
    if root is None:
        api.ui.notify("Not in a git repository", level="info")
        return
    try:
        api.git.commit(message, path=root)
    except Exception as exc:
        api.ui.notify(f"Commit failed: {exc}", level="error")
        return
    _refresh_panel(api)
    api.ui.notify(f"Committed: {message[:60]}", level="info")


def _cmd_stage_file(api: Any, file_path: str) -> None:
    if not file_path:
        api.ui.notify("GitStageFile expects a file path", level="info")
        return
    root = _git_root(api)
    if root is None:
        api.ui.notify("Not in a git repository", level="info")
        return
    try:
        api.git.stage_file(file_path, path=root)
    except Exception as exc:
        api.ui.notify(f"Stage failed: {exc}", level="error")
        return
    _refresh_panel(api)
    _refresh_signs_for_path(api, file_path)
    api.ui.notify(f"Staged: {file_path}", level="info")
    api.ui.focus_sidebar()


def _cmd_unstage_file(api: Any, file_path: str) -> None:
    if not file_path:
        api.ui.notify("GitUnstageFile expects a file path", level="info")
        return
    root = _git_root(api)
    if root is None:
        api.ui.notify("Not in a git repository", level="info")
        return
    try:
        api.git.unstage_file(file_path, path=root)
    except Exception as exc:
        api.ui.notify(f"Unstage failed: {exc}", level="error")
        return
    _refresh_panel(api)
    _refresh_signs_for_path(api, file_path)
    api.ui.notify(f"Unstaged: {file_path}", level="info")
    api.ui.focus_sidebar()


def _cmd_discard_file(api: Any, file_path: str) -> None:
    if not file_path:
        api.ui.notify("GitDiscardFile expects a file path", level="info")
        return
    root = _git_root(api)
    if root is None:
        api.ui.notify("Not in a git repository", level="info")
        return
    try:
        api.git.discard_file(file_path, path=root)
    except Exception as exc:
        api.ui.notify(f"Discard failed: {exc}", level="error")
        return
    _refresh_panel(api)
    _refresh_signs_for_path(api, file_path)
    api.ui.notify(f"Discarded changes: {file_path}", level="info")
    api.ui.focus_sidebar()


def _refresh_signs_for_path(api: Any, file_path: str) -> None:
    """Refresh gitsigns decorations for a specific file path."""
    target = Path(file_path).resolve()
    for buf in api.list_buffers():
        buf_path = getattr(buf, "path", None)
        if buf_path is not None and Path(buf_path).resolve() == target:
            _update_signs(api, buf)
            return


def _show_panel_help(api: Any) -> None:
    api.ui.notify(_panel_help_text(), level="info", timeout=8.0)


def _panel_help_text() -> str:
    return "\n".join(
        [
            "Git panel keys:",
            "  ? help",
            "  R refresh",
            "  C commit (type message after GitCommit)",
            "  c create branch",
            "  f fetch",
            "  p pull",
            "  P push",
            "  l open log for current/selected branch",
            "  <CR> open selected status file",
            "  d diff selected status file",
            "  a/u/x stage, unstage, discard file",
            "  s checkout selected local branch",
            "  m merge selected local or remote branch",
        ]
    )


def _toggle_status_panel(api: Any) -> None:
    panel = api.ui.get_sidebar_panel("git-status")
    if panel is None:
        panel = _GitStatusSidebarPanel(api)
        api.ui.register_sidebar_panel("git-status", panel)
    if api.ui.is_sidebar_visible("git-status"):
        api.ui.hide_sidebar()
        return
    if hasattr(panel, "refresh"):
        panel.refresh()
    api.ui.show_sidebar_panel("git-status", panel, focus=True)


def _refresh_panel(api: Any) -> None:
    panel = api.ui.get_sidebar_panel("git-status")
    if panel is not None and hasattr(panel, "refresh"):
        panel.refresh()


def _build_status_nodes(api: Any) -> tuple[str, list[TreeNode]]:
    from peovim.ui.tree_view import TreeNode

    root = _git_root(api)
    if root is None:
        return "Git", [TreeNode(label="Not in a git repository")]

    try:
        state = api.git.repo_state(root)
    except Exception:
        state = None
    if state is None:
        return "Git", [TreeNode(label="Not in a git repository")]

    try:
        branches = api.git.list_branches(root, include_remote=True)
    except Exception:
        branches = []

    branch = state.branch.name
    title = f"Git [{branch}]" if branch else "Git"

    summary_nodes = _summary_nodes(state)
    branch_nodes = _branch_nodes(branches)
    status_nodes = _status_nodes(root, state.status)
    remote_nodes = _remote_nodes(state.remotes)

    nodes = [
        _section_node("Summary", summary_nodes),
        _section_node("Branches", branch_nodes),
        _section_node("Status", status_nodes),
        _section_node("Remotes", remote_nodes),
    ]
    return title, nodes


def _section_node(label: str, children: list[TreeNode]) -> TreeNode:
    from peovim.ui.tree_view import TreeNode

    node = TreeNode(label=label, children_fn=lambda items=children: items)  # type: ignore[misc]
    node.expanded = True
    return node


def _summary_nodes(state: Any) -> list[TreeNode]:
    from peovim.ui.tree_view import TreeNode

    tracking_bits: list[str] = []
    if state.branch.upstream:
        tracking_bits.append(state.branch.upstream)
    if state.branch.ahead:
        tracking_bits.append(f"ahead {state.branch.ahead}")
    if state.branch.behind:
        tracking_bits.append(f"behind {state.branch.behind}")
    if state.branch.gone:
        tracking_bits.append("gone")
    tracking = f" ({', '.join(tracking_bits)})" if tracking_bits else ""

    staged_count = sum(1 for e in state.status if not e.untracked and e.index_status not in (" ", "?"))
    unstaged_count = sum(1 for e in state.status if not e.untracked and e.worktree_status not in (" ", "?"))
    untracked_count = sum(1 for e in state.status if e.untracked)
    sync_label = _sync_summary_label(state.branch)
    if state.status:
        parts = []
        if staged_count:
            parts.append(f"staged {staged_count}")
        if unstaged_count:
            parts.append(f"unstaged {unstaged_count}")
        if untracked_count:
            parts.append(f"untracked {untracked_count}")
        summary = ", ".join(parts) if parts else f"{len(state.status)} changed"
    else:
        summary = "working tree clean"
    return [
        TreeNode(label=f"Root: {state.root}"),
        TreeNode(label=f"Branch: {state.branch.name or 'HEAD'}{tracking}"),
        TreeNode(label=f"Sync: {sync_label}"),
        TreeNode(label=f"Summary: {summary}"),
    ]


def _branch_nodes(branches: list[Any]) -> list[TreeNode]:
    from peovim.ui.tree_view import TreeNode

    if not branches:
        return [TreeNode(label="No branches")]
    nodes = []
    for branch in branches:
        prefix = "*" if branch.current else " "
        tracking = f" → {branch.upstream}" if branch.upstream else ""
        ahead_behind = []
        if branch.ahead:
            ahead_behind.append(f"ahead {branch.ahead}")
        if branch.behind:
            ahead_behind.append(f"behind {branch.behind}")
        suffix_bits: list[str] = []
        if getattr(branch, "remote", False):
            suffix_bits.append("remote")
        if ahead_behind:
            suffix_bits.append(", ".join(ahead_behind))
        suffix = f" [{'; '.join(suffix_bits)}]" if suffix_bits else ""
        nodes.append(
            TreeNode(
                label=f"{prefix} {branch.name}{tracking}{suffix}",
                value=("branch", branch.name, bool(getattr(branch, "remote", False))),
            )
        )
    return nodes


def _status_nodes(root: Path, entries: list[Any]) -> list[TreeNode]:
    from peovim.ui.tree_view import TreeNode

    if not entries:
        return [TreeNode(label="Working tree clean")]

    staged: list[Any] = []
    unstaged: list[Any] = []
    untracked: list[Any] = []

    for entry in entries:
        if entry.untracked:
            untracked.append(entry)
        else:
            if entry.index_status not in (" ", "?"):
                staged.append(entry)
            if entry.worktree_status not in (" ", "?"):
                unstaged.append(entry)

    def _entry_node(entry: Any, label_prefix: str = "") -> TreeNode:
        target = (root / entry.path).resolve()
        return TreeNode(
            label=f"{label_prefix}{entry.display_path}",
            value=("status", str(target)),
            fg=color_for_status_entry(entry, surface="panel"),
        )

    def _group(title: str, items: list[Any], prefix: str = "") -> TreeNode:
        children = [_entry_node(e, prefix) for e in items]
        node = TreeNode(label=f"{title} ({len(items)})", children_fn=lambda c=children: c)  # type: ignore[misc]
        node.expanded = True
        return node

    nodes: list[TreeNode] = []
    if staged:
        nodes.append(_group("Staged", staged))
    if unstaged:
        nodes.append(_group("Unstaged", unstaged))
    if untracked:
        nodes.append(_group("Untracked", untracked))
    return nodes


def _remote_nodes(remotes: list[Any]) -> list[TreeNode]:
    from peovim.ui.tree_view import TreeNode

    if not remotes:
        return [TreeNode(label="No remotes")]
    return [TreeNode(label=f"{remote.name}: {remote.url}") for remote in remotes]


def _selected_branch_ref(node: Any) -> str | None:
    value = getattr(node, "value", None)
    if isinstance(value, tuple) and len(value) >= 2 and value[0] == "branch":
        return str(value[1])
    return None


def _selected_checkout_branch_name(node: Any) -> str | None:
    value = getattr(node, "value", None)
    if isinstance(value, tuple) and len(value) >= 3 and value[0] == "branch" and not bool(value[2]):
        return str(value[1])
    if isinstance(value, tuple) and len(value) == 2 and value[0] == "branch":
        return str(value[1])
    return None


def _selected_status_path(node: Any) -> str | None:
    value = getattr(node, "value", None)
    if isinstance(value, tuple) and len(value) == 2 and value[0] == "status":
        return str(value[1])
    if isinstance(value, str):
        return value
    return None


def _sync_summary_label(branch: Any) -> str:
    pieces: list[str] = []
    if getattr(branch, "upstream", None):
        pieces.append(str(branch.upstream))
    ahead = int(getattr(branch, "ahead", 0) or 0)
    behind = int(getattr(branch, "behind", 0) or 0)
    if ahead:
        pieces.append(f"ahead {ahead}")
    if behind:
        pieces.append(f"behind {behind}")
    if getattr(branch, "gone", False):
        pieces.append("gone")
    if not pieces:
        return "up to date"
    return " · ".join(pieces)


def _parse_upstream(branch: Any) -> tuple[str | None, str | None]:
    upstream = getattr(branch, "upstream", None)
    if not upstream or "/" not in str(upstream):
        return None, None
    remote, branch_name = str(upstream).split("/", 1)
    return remote or None, branch_name or None


def _current_repo_state(api: Any) -> Any | None:
    root = _git_root(api)
    if root is None:
        return None
    try:
        return api.git.repo_state(root)
    except Exception:
        return None


def _fetch_cmdline(api: Any) -> str:
    state = _current_repo_state(api)
    if state is None:
        return "GitFetch "
    remote, _branch = _parse_upstream(state.branch)
    return f"GitFetch {remote}".rstrip() if remote else "GitFetch "


def _pull_cmdline(api: Any) -> str:
    state = _current_repo_state(api)
    if state is None:
        return "GitPull "
    remote, branch_name = _parse_upstream(state.branch)
    parts = [part for part in ["GitPull", remote, branch_name] if part]
    return " ".join(parts) if len(parts) > 1 else "GitPull "


def _push_cmdline(api: Any) -> str:
    state = _current_repo_state(api)
    if state is None:
        return "GitPush "
    remote, branch_name = _parse_upstream(state.branch)
    local_branch = getattr(state.branch, "name", None)
    parts = [part for part in ["GitPush", remote, branch_name or local_branch] if part]
    return " ".join(parts) if len(parts) > 1 else "GitPush "


def _cmd_branch_create(api: Any, name: str) -> None:
    if not name:
        api.ui.notify("GitBranchCreate expects a branch name", level="info")
        return
    root = _git_root(api)
    if root is None:
        api.ui.notify("Not in a git repository", level="info")
        return
    try:
        api.git.create_branch(name, path=root)
    except Exception as exc:
        api.ui.notify(f"Create branch failed: {exc}", level="error")
        return
    _refresh_panel(api)
    api.ui.notify(f"Created branch: {name}", level="info")


def _cmd_checkout(api: Any, ref: str) -> None:
    if not ref:
        api.ui.notify("GitCheckout expects a branch or ref", level="info")
        return
    root = _git_root(api)
    if root is None:
        api.ui.notify("Not in a git repository", level="info")
        return
    try:
        api.git.checkout(ref, path=root)
    except Exception as exc:
        api.ui.notify(f"Checkout failed: {exc}", level="error")
        return
    _refresh_panel(api)
    api.ui.notify(f"Checked out: {ref}", level="info")


def _cmd_merge_branch(api: Any, ref: str) -> None:
    if not ref:
        api.ui.notify("GitMergeBranch expects a branch name", level="info")
        return
    root = _git_root(api)
    if root is None:
        api.ui.notify("Not in a git repository", level="info")
        return
    try:
        api.git.merge(ref, path=root)
    except Exception as exc:
        api.ui.notify(f"Merge failed: {exc}", level="error")
        return
    _refresh_panel(api)
    api.ui.notify(f"Merged branch: {ref}", level="info")


def _cmd_fetch(api: Any, args: str) -> None:
    root = _git_root(api)
    if root is None:
        api.ui.notify("Not in a git repository", level="info")
        return
    remote = args or None
    try:
        api.git.fetch(path=root, remote=remote)
    except Exception as exc:
        api.ui.notify(f"Fetch failed: {exc}", level="error")
        return
    _refresh_panel(api)
    api.ui.notify(f"Fetched {remote or 'all remotes'}", level="info")


def _cmd_pull(api: Any, args: str) -> None:
    root = _git_root(api)
    if root is None:
        api.ui.notify("Not in a git repository", level="info")
        return
    parts = [part for part in args.split() if part]
    remote = parts[0] if parts else None
    branch = parts[1] if len(parts) > 1 else None
    try:
        api.git.pull(path=root, remote=remote, branch=branch)
    except Exception as exc:
        api.ui.notify(f"Pull failed: {exc}", level="error")
        return
    _refresh_panel(api)
    target = " ".join(part for part in [remote, branch] if part) or "current upstream"
    api.ui.notify(f"Pulled {target}", level="info")


def _cmd_push(api: Any, args: str) -> None:
    root = _git_root(api)
    if root is None:
        api.ui.notify("Not in a git repository", level="info")
        return
    parts = [part for part in args.split() if part]
    remote = parts[0] if parts else None
    branch = parts[1] if len(parts) > 1 else None
    try:
        api.git.push(path=root, remote=remote, branch=branch)
    except Exception as exc:
        api.ui.notify(f"Push failed: {exc}", level="error")
        return
    _refresh_panel(api)
    target = " ".join(part for part in [remote, branch] if part) or "current upstream"
    api.ui.notify(f"Pushed {target}", level="info")


def _cmd_log(api: Any, args: str) -> bool:
    root = _git_root(api)
    if root is None:
        api.ui.notify("Not in a git repository", level="info")
        return False
    state = _current_repo_state(api)
    ref = args.strip() or getattr(getattr(state, "branch", None), "name", None) or "HEAD"
    entries = api.git.log_entries(path=root, limit=50, ref=ref)
    if not entries:
        api.ui.notify(f"No commits found for {ref}", level="info")
        return False

    snapshot_path = _write_git_snapshot(
        root, "logs", f"{_snapshot_name(ref)}.log", _render_log_text(root, ref, entries)
    )
    api.open_buffer(snapshot_path)
    api.ui.notify(f"Opened git log: {ref}", level="info")
    return True


def _cmd_compare_file(api: Any, args: str) -> bool:
    target_arg = args.strip()
    if not target_arg:
        api.ui.notify("GitDiffFile expects a file path", level="info")
        return False
    root = _git_root(api)
    if root is None:
        api.ui.notify("Not in a git repository", level="info")
        return False
    state = _current_repo_state(api)
    if state is None:
        api.ui.notify("Not in a git repository", level="info")
        return False

    target_path = Path(target_arg).resolve()
    entry = _status_entry_for_path(root, state.status, target_path)
    if entry is None:
        api.ui.notify(f"No git status entry for: {target_path}", level="info")
        return False

    if api.events.handler_count("diff_selection_ready") == 0:
        api.ui.notify("Diff viewer not available (add peovim.plugins.compare to init.py)", level="info")
        return False

    left_path, right_path = _compare_paths_for_status(api, root, entry, target_path)
    api.events.emit("diff_selection_ready", left=str(left_path), right=str(right_path))
    return True


def _git_root(api: Any) -> Path | None:
    buf = api.active_buffer()
    path = getattr(buf, "path", None)
    if path is not None:
        root = api.git.root(Path(path).parent if Path(path).is_file() else Path(path))
        if root is not None:
            return root
    return api.git.root()


def _status_entry_for_path(root: Path, entries: list[Any], target_path: Path) -> Any | None:
    for entry in entries:
        if (root / entry.path).resolve() == target_path:
            return entry
    return None


def _compare_paths_for_status(api: Any, root: Path, entry: Any, target_path: Path) -> tuple[Path, Path]:
    base_repo_path = getattr(entry, "original_path", None) or entry.path
    base_text = api.git.show_file_text(base_repo_path, path=root, ref="HEAD")
    left_bucket = "head" if base_text is not None else "empty"
    left_path = _write_compare_snapshot(root, left_bucket, base_repo_path, base_text or "")

    right_path = target_path if target_path.exists() else _write_compare_snapshot(root, "working-tree", entry.path, "")
    return left_path, right_path


def _write_compare_snapshot(root: Path, bucket: str, repo_path: str, text: str) -> Path:
    snapshot_path = _project_scratch_path(root, "git-compare", bucket, repo_path)
    # save policy: single-writer (path keyed by git hash bucket; content-addressed, no conflict)
    atomic_write_text(snapshot_path, text, encoding="utf-8")
    return snapshot_path


def _write_git_snapshot(root: Path, bucket: str, relative_path: str, text: str) -> Path:
    snapshot_path = _project_scratch_path(root, "git", bucket, relative_path)
    # save policy: single-writer (path keyed by git hash bucket; content-addressed, no conflict)
    atomic_write_text(snapshot_path, text, encoding="utf-8")
    return snapshot_path


def _snapshot_name(value: str) -> str:
    slug = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value) or "HEAD"
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    return f"{slug}--{digest}"


def _project_scratch_path(root: Path, category: str, bucket: str, relative_path: str | Path) -> Path:
    base = (root / ".peovim" / category / bucket).resolve()
    relative = _safe_relative_project_path(relative_path)
    return (base / relative).resolve()


def _safe_relative_project_path(value: str | Path) -> Path:
    parts: list[str] = []
    for part in PurePosixPath(str(value).replace("\\", "/")).parts:
        if part in {"", "/", "."}:
            continue
        if part == "..":
            parts.append("_")
            continue
        parts.append(part)
    return Path(*parts) if parts else Path("scratch")


def _render_log_text(root: Path, ref: str, entries: list[Any]) -> str:
    lines = [f"Git log for {ref}", f"Repository: {root}", ""]
    for entry in entries:
        refs = f" [{entry.refs}]" if getattr(entry, "refs", "") else ""
        lines.append(f"{entry.short_commit}{refs}  {entry.subject}")
        lines.append(f"  {entry.author} · {entry.relative_date}")
        lines.append(f"  {entry.commit}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
