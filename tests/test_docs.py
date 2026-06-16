"""
CI guard: python_overview.md must mention every .py file under peovim/.

Add a one-line entry to python_overview.md when you add a new module.
See notes/developer_guide.md for the checklist.
"""

from pathlib import Path

_ROOT = Path(__file__).parent.parent
_PEOVIM = _ROOT / "peovim"
_OVERVIEW = _ROOT / "notes" / "python_overview.md"

_IGNORED_SUFFIXES = {"/__init__.py"}
_IGNORED_PATHS = {
    "_native/__init__.py",
}


def _all_modules() -> set[str]:
    return {
        p.relative_to(_PEOVIM).as_posix()
        for p in _PEOVIM.rglob("*.py")
        if not any(p.relative_to(_PEOVIM).as_posix().endswith(s) for s in _IGNORED_SUFFIXES)
        and p.relative_to(_PEOVIM).as_posix() not in _IGNORED_PATHS
    }


def _documented_modules() -> set[str]:
    import re

    text = _OVERVIEW.read_text(encoding="utf-8")
    mentioned: set[str] = set()
    for m in re.finditer(r"`([^`]+\.py)`", text):
        mentioned.add(m.group(1))
    return mentioned


def test_python_overview_covers_all_modules():
    all_mods = _all_modules()
    documented = _documented_modules()

    # Check using just the basename since the overview uses short names
    documented_basenames = {m.split("/")[-1] for m in documented}
    all_basenames = {m.split("/")[-1] for m in all_mods}

    missing_basenames = all_basenames - documented_basenames
    assert not missing_basenames, (
        f"python_overview.md is missing entries for {len(missing_basenames)} module(s):\n"
        + "\n".join(f"  {n}" for n in sorted(missing_basenames))
        + "\n\nAdd a one-line entry to notes/python_overview.md."
    )
