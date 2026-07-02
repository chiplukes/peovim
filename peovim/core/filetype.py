"""
core.filetype — Filetype detection from path extension and shebang line.

detect_filetype(path, first_line) -> str
  Returns a lowercase filetype string (e.g. 'python', 'rust', '')
  or '' if the filetype cannot be determined.
"""

from __future__ import annotations

_EXT_MAP: dict[str, str] = {
    # Python
    "py": "python",
    "pyi": "python",
    "pyw": "python",
    # JavaScript / TypeScript
    "js": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "jsx": "jsx",
    "ts": "typescript",
    "mts": "typescript",
    "cts": "typescript",
    "tsx": "tsx",
    # Rust
    "rs": "rust",
    # C / C++
    "c": "c",
    "h": "c",
    "cpp": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "hpp": "cpp",
    "hh": "cpp",
    "hxx": "cpp",
    # Verilog / SystemVerilog
    "v": "verilog",
    "vh": "verilog",
    "sv": "verilog",
    "svh": "verilog",
    # Go
    "go": "go",
    # Lua
    "lua": "lua",
    # Ruby
    "rb": "ruby",
    "rake": "ruby",
    "gemspec": "ruby",
    # Shell
    "sh": "bash",
    "bash": "bash",
    "zsh": "bash",
    # Java / Kotlin
    "java": "java",
    "kt": "kotlin",
    "kts": "kotlin",
    # C#
    "cs": "csharp",
    # Swift
    "swift": "swift",
    # Zig
    "zig": "zig",
    # Haskell
    "hs": "haskell",
    "lhs": "haskell",
    # OCaml
    "ml": "ocaml",
    "mli": "ocaml",
    # R
    "r": "r",
    # Elixir
    "ex": "elixir",
    "exs": "elixir",
    # Erlang
    "erl": "erlang",
    "hrl": "erlang",
    # Clojure
    "clj": "clojure",
    "cljs": "clojure",
    "cljc": "clojure",
    # Scala
    "scala": "scala",
    "sc": "scala",
    # PHP
    "php": "php",
    # FPGA constraints
    "xdc": "xdc",
    "sdc": "xdc",
    # Data / Config
    "json": "json",
    "jsonc": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "toml": "toml",
    "xml": "xml",
    "html": "html",
    "htm": "html",
    "css": "css",
    "scss": "scss",
    "sass": "scss",
    "sql": "sql",
    # Vim
    "vim": "vim",
    # Markdown / text
    "md": "markdown",
    "markdown": "markdown",
    "mdx": "markdown",
    "rst": "rst",
    "txt": "text",
    # Docker
    "dockerfile": "dockerfile",
    # Makefile (no extension — handled by basename below)
    "mk": "make",
    # Terraform
    "tf": "terraform",
    "tfvars": "terraform",
    # Nix
    "nix": "nix",
    # Proto
    "proto": "proto",
}

_BASENAME_MAP: dict[str, str] = {
    "dockerfile": "dockerfile",
    "makefile": "make",
    "gnumakefile": "make",
    "rakefile": "ruby",
    "gemfile": "ruby",
    "brewfile": "ruby",
    "vagrantfile": "ruby",
    "justfile": "make",
    # Shell dotfiles (no extension)
    ".bashrc": "bash",
    ".bash_profile": "bash",
    ".bash_aliases": "bash",
    ".bash_logout": "bash",
    ".bash_history": "bash",
    ".zshrc": "bash",
    ".zprofile": "bash",
    ".zshenv": "bash",
    ".zlogin": "bash",
    ".zlogout": "bash",
    ".profile": "bash",
    ".kshrc": "bash",
    ".mkshrc": "bash",
}

_SHEBANG_MAP: list[tuple[str, str]] = [
    ("python", "python"),
    ("python3", "python"),
    ("python2", "python"),
    ("node", "javascript"),
    ("nodejs", "javascript"),
    ("deno", "typescript"),
    ("ruby", "ruby"),
    ("perl", "perl"),
    ("lua", "lua"),
    ("bash", "bash"),
    ("/sh", "bash"),
    ("zsh", "bash"),
    ("fish", "bash"),
    ("tcsh", "bash"),
    ("ksh", "bash"),
    ("r ", "r"),
    ("rscript", "r"),
    ("php", "php"),
    ("elixir", "elixir"),
]


def detect_filetype(path: str | None, first_line: str = "") -> str:
    """
    Return a filetype string from the file path extension and/or shebang line.

    Args:
        path:       File path (may be None for scratch buffers).
        first_line: First line of the file content (for shebang detection).

    Returns:
        A lowercase filetype string (e.g. 'python', 'rust') or '' if unknown.
    """
    # 1. Extension-based detection
    if path:
        from pathlib import Path as _Path

        p = _Path(path)
        # Check bare basename first (e.g. "Makefile", "Dockerfile")
        basename_ft = _BASENAME_MAP.get(p.name.lower())
        if basename_ft:
            return basename_ft
        # Extension (without leading dot, lowercased)
        ext = p.suffix.lstrip(".").lower()
        if ext:
            ext_ft = _EXT_MAP.get(ext)
            if ext_ft:
                return ext_ft

    # 2. Shebang detection
    line = first_line.strip()
    if line.startswith("#!"):
        lower = line.lower()
        for token, ft in _SHEBANG_MAP:
            if token in lower:
                return ft

    return ""
