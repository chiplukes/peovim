"""
modal.keybindings — BindingRegistry: user-defined key binding storage and registration.

Wraps ModalEngine.add_user_binding() and stores metadata for which-key display.

<Plug> mappings
---------------
Plugins register public actions with define_plug (via KeymapAPI.define_plug):
    api.keymap.define_plug("GitsignsNextHunk", fn, desc="Git: next hunk")

Users remap them in init.py without needing to import internal functions:
    keymap.nmap("]h", "<Plug>GitsignsNextHunk", desc="Next hunk")

<Plug> targets are resolved directly inside BindingRegistry — they never
go through the engine trie, so no key-sequence parsing issues.
"""

from __future__ import annotations

import contextlib
import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.modal.dispatcher import ActionDispatcher
    from peovim.modal.engine import ModalEngine


def _build_ctx(engine: Any, state: Any) -> Any:
    """Snapshot engine + parse state into a PluginContext at binding-fire time."""
    from peovim.modal.actions import PluginContext
    from peovim.modal.engine import Mode

    mode = engine.mode
    cursor = engine._cursor
    anchor = engine._visual_anchor
    visual_modes = (Mode.VISUAL_CHAR, Mode.VISUAL_LINE, Mode.VISUAL_BLOCK)
    if mode in visual_modes:
        start = min(anchor[0], cursor[0])
        end = max(anchor[0], cursor[0])
        vr: tuple[int, int] | None = (start, end)
        vlc = end - start + 1
    else:
        vr = None
        vlc = 1
    mode_str = mode.name.lower()  # e.g. "normal", "visual_char"
    count = max(1, state.effective_count_a()) if state.count_a > 0 else 1
    return PluginContext(
        mode=mode_str,
        visual_range=vr,
        count=count,
        register=state.register or "",
        cursor=cursor,
        is_repeat=False,
        visual_line_count=vlc,
    )


@dataclass
class BindingInfo:
    mode: str
    keys: str
    desc: str
    noremap: bool


@dataclass
class _RegisteredBinding:
    mode: str
    keys: str
    action_fn: Any
    noremap: bool
    engine_keys: str


class BindingRegistry:  # cm:8c3a1f
    """
    Stores user-defined key bindings and registers them with the ModalEngine.

    target is a Python callable → RunPlugin action.
    target is "<Plug>Name"      → resolves to the registered plug callback.
    target is any other string  → RunNormalKeys (key sequence replay).
    """

    def __init__(self, engine: ModalEngine, dispatcher: ActionDispatcher) -> None:
        self._engine = engine
        self._dispatcher = dispatcher
        self._bindings: list[BindingInfo] = []
        self._groups: dict[str, str] = {}  # prefix → human name
        self._group_defs: dict[str, str] = {}
        self._plug_registry: dict[str, int] = {}  # plug_name → callback_id
        self._plug_to_keys: dict[str, list[tuple[str, str]]] = {}  # plug_name → [(mode, keys)]
        self._registered: dict[tuple[str, str], _RegisteredBinding] = {}
        self._next_id: int = 0
        self._subscribe_option_changes()

    def _get_leader(self) -> str:
        es = getattr(self._dispatcher, "_editor_state", None)
        if es is not None and hasattr(es, "options"):
            return es.options.get("leader") or "\\"
        return "\\"

    def _get_local_leader(self) -> str:
        es = getattr(self._dispatcher, "_editor_state", None)
        if es is not None and hasattr(es, "options"):
            return es.options.get("localleader") or "\\"
        return "\\"

    def _expand_special_keys(self, keys: str) -> str:
        leader = self._get_leader()
        local_leader = self._get_local_leader()
        return (
            keys.replace("<localleader>", local_leader)
            .replace("<LocalLeader>", local_leader)
            .replace("<leader>", leader)
            .replace("<Leader>", leader)
        )

    def _subscribe_option_changes(self) -> None:
        es = getattr(self._dispatcher, "_editor_state", None)
        event_bus = getattr(es, "event_bus", None)
        if event_bus is None:
            return
        with contextlib.suppress(Exception):
            event_bus.on("option_changed", self._on_option_changed)

    def _on_option_changed(self, name: str = "", scope: str = "global", **_kwargs: Any) -> None:
        if scope != "global" or name not in {"leader", "localleader"}:
            return
        self._rebind_special_bindings()
        self._rebuild_groups()

    def _rebind_special_bindings(self) -> None:
        for registered in self._registered.values():
            if (
                "<leader>" not in registered.keys
                and "<Leader>" not in registered.keys
                and "<localleader>" not in registered.keys
                and "<LocalLeader>" not in registered.keys
            ):
                continue
            engine_mode = self._mode_to_engine_mode(registered.mode)
            if engine_mode is None:
                continue
            self._engine.remove_user_binding(engine_mode, registered.engine_keys)
            registered.engine_keys = self._expand_special_keys(registered.keys)
            self._engine.add_user_binding(
                engine_mode,
                registered.engine_keys,
                registered.action_fn,
                noremap=registered.noremap,
            )

    def _rebuild_groups(self) -> None:
        self._groups = {}
        for keys, name in self._group_defs.items():
            self._groups[keys] = name
            self._groups[self._expand_special_keys(keys)] = name

    @staticmethod
    def _mode_to_engine_mode(mode: str) -> Any:
        from peovim.modal.engine import Mode

        mode_map = {
            "normal": Mode.NORMAL,
            "insert": Mode.INSERT,
            "visual": Mode.VISUAL_CHAR,
        }
        return mode_map.get(mode)

    def register(self, mode: str, keys: str, target: Any, *, noremap: bool = True, desc: str = "") -> None:
        """Register a key binding."""
        from peovim.modal.actions import RunNormalKeys, RunPlugin

        engine_mode = self._mode_to_engine_mode(mode)
        if engine_mode is None:
            return

        if callable(target):
            cb_id = self._next_id
            self._next_id += 1
            self._dispatcher._plugin_callbacks[cb_id] = target

            # If the key sequence is a <Plug> name, also register it in plug_registry
            if keys.startswith("<Plug>"):
                plug_name = keys[len("<Plug>") :]
                self._plug_registry[plug_name] = cb_id

            def action_fn(state: Any, _cb_id: int = cb_id) -> list:
                return [RunPlugin(_cb_id, _build_ctx(self._engine, state))]

        elif isinstance(target, str) and target.startswith("<Plug>"):
            # Remap to a registered <Plug> — resolve at call time so order doesn't matter
            plug_name = target[len("<Plug>") :]

            # Track reverse mapping so find_keys_for_plug() can look this up
            entries = self._plug_to_keys.setdefault(plug_name, [])
            entries[:] = [(m, k) for m, k in entries if not (m == mode and k == keys)]
            entries.append((mode, keys))

            def action_fn(state: Any, _name: str = plug_name) -> list:  # type: ignore[misc]
                cb_id = self._plug_registry.get(_name)
                if cb_id is not None:
                    return [RunPlugin(cb_id, _build_ctx(self._engine, state))]
                return []

        else:

            def action_fn(state: Any, _tgt: str = target) -> list:  # type: ignore[misc]
                return [RunNormalKeys(_tgt, remap=True)]

        # <Plug> keys are registered verbatim (single token) — skip for engine
        if not keys.startswith("<Plug>"):
            existing = self._registered.get((mode, keys))
            if existing is not None:
                self._engine.remove_user_binding(engine_mode, existing.engine_keys)
            engine_keys = self._expand_special_keys(keys)
            self._engine.add_user_binding(engine_mode, engine_keys, action_fn, noremap=noremap)
            self._registered[(mode, keys)] = _RegisteredBinding(
                mode=mode,
                keys=keys,
                action_fn=action_fn,
                noremap=noremap,
                engine_keys=engine_keys,
            )

        # Store in _bindings for which-key display; skip <Plug> internals.
        # Remove any previous entry for the same mode+keys so re-registering
        # (e.g. user config overriding a plugin default) doesn't create duplicates
        # that confuse which-key into showing "+group" instead of a leaf label.
        if not keys.startswith("<Plug>"):
            self._bindings = [b for b in self._bindings if not (b.mode == mode and b.keys == keys)]
            self._bindings.append(BindingInfo(mode=mode, keys=keys, desc=desc, noremap=noremap))

    def register_plug(self, mode: str, plug_name: str, target: Any, desc: str = "") -> None:
        """Register a <Plug> target (callable only). Shortcut for define_plug."""
        self.register(mode, f"<Plug>{plug_name}", target, desc=desc)

    @staticmethod
    def _callback_takes_context(callback: Any) -> bool:
        try:
            signature = inspect.signature(callback)
        except (TypeError, ValueError):
            return False

        positional_kinds = {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        }
        return any(
            parameter.kind in positional_kinds and parameter.default is inspect.Parameter.empty
            for parameter in signature.parameters.values()
        )

    def _snapshot_plugin_context(self) -> Any:
        from peovim.modal.actions import PluginContext
        from peovim.modal.engine import Mode

        mode = self._engine.mode
        cursor = self._engine._cursor
        anchor = self._engine._visual_anchor
        visual_modes = (Mode.VISUAL_CHAR, Mode.VISUAL_LINE, Mode.VISUAL_BLOCK)
        if mode in visual_modes:
            start = min(anchor[0], cursor[0])
            end = max(anchor[0], cursor[0])
            visual_range: tuple[int, int] | None = (start, end)
            visual_line_count = end - start + 1
        else:
            visual_range = None
            visual_line_count = 1
        return PluginContext(
            mode=mode.name.lower(),
            visual_range=visual_range,
            count=1,
            register="",
            cursor=cursor,
            is_repeat=False,
            visual_line_count=visual_line_count,
        )

    def execute_plug(self, name: str) -> bool:
        """Execute a registered <Plug> callback immediately."""
        plug_name = name[len("<Plug>") :] if name.startswith("<Plug>") else name
        cb_id = self._plug_registry.get(plug_name)
        if cb_id is None:
            return False
        callback = self._dispatcher._plugin_callbacks.get(cb_id)
        if callback is None:
            return False
        if self._callback_takes_context(callback):
            callback(self._snapshot_plugin_context())
        else:
            callback()
        return True

    def unregister(self, mode: str, keys: str) -> None:
        """Remove a key binding from the engine trie and the binding list."""
        engine_mode = self._mode_to_engine_mode(mode)
        if engine_mode is None:
            return
        if not keys.startswith("<Plug>"):
            existing = self._registered.pop((mode, keys), None)
            engine_keys = existing.engine_keys if existing is not None else self._expand_special_keys(keys)
            self._engine.remove_user_binding(engine_mode, engine_keys)
        self._bindings = [b for b in self._bindings if not (b.mode == mode and b.keys == keys)]
        # Clean up reverse plug mapping
        for entries in self._plug_to_keys.values():
            entries[:] = [(m, k) for m, k in entries if not (m == mode and k == keys)]

    def find_keys_for_plug(self, plug_name: str, mode: str = "normal") -> list[str]:
        """Return all key sequences in *mode* currently bound to <Plug>plug_name."""
        return [k for m, k in self._plug_to_keys.get(plug_name, []) if m == mode]

    def lookup(self, mode: str, keys: str) -> BindingInfo | None:
        for b in self._bindings:
            if b.mode == mode and b.keys == keys:
                return b
        return None

    def get_bindings(self, mode: str | None = None) -> list[BindingInfo]:
        if mode is None:
            return list(self._bindings)
        return [b for b in self._bindings if b.mode == mode]

    def register_group(self, keys: str, name: str) -> None:
        self._group_defs[keys] = name
        self._rebuild_groups()

    def get_group_name(self, prefix: str) -> str:
        return self._groups.get(prefix, "")
