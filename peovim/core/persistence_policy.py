"""Shared description of persistence surfaces and their concurrency policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PersistencePolicyItem:
    """Describes how one persisted surface behaves across editor instances."""

    name: str
    scope: str
    storage: str
    write_mode: str
    coordination: str
    guidance: str


def persistence_policy_items() -> list[PersistencePolicyItem]:
    """Return the current persistence-policy inventory used by docs and health output."""
    return [
        PersistencePolicyItem(
            name="File saves",
            scope="per file",
            storage="user file on disk",
            write_mode="atomic replace",
            coordination="save-time external-change detection; no merge",
            guidance="Normal save stops if the file changed on disk; use :e to reload or :w! to overwrite.",
        ),
        PersistencePolicyItem(
            name="shada",
            scope="global user data",
            storage="platform data dir / shada",
            write_mode="atomic replace",
            coordination="last-writer-wins",
            guidance="Safe against partial writes, but concurrent instances can still overwrite each other's history/marks.",
        ),
        PersistencePolicyItem(
            name="sessions",
            scope="global named sessions",
            storage="platform data dir / sessions/*.json",
            write_mode="atomic replace",
            coordination="last-writer-wins per session name",
            guidance="Treat session files as convenience snapshots, not a coordinated workspace database.",
        ),
        PersistencePolicyItem(
            name="plugin stores",
            scope="global per plugin",
            storage="platform data dir / stores/*.json",
            write_mode="atomic replace",
            coordination="last-writer-wins per plugin store",
            guidance="Good for lightweight preferences/state, but not safe for concurrent merge-heavy updates.",
        ),
        PersistencePolicyItem(
            name="project markers",
            scope="project local",
            storage="<root>/.peovim/markers.json",
            write_mode="atomic replace",
            coordination="single-writer-friendly; no lock/merge",
            guidance="Avoid editing the same project marker data from multiple editor instances at once.",
        ),
        PersistencePolicyItem(
            name="git scratch snapshots",
            scope="project local scratch",
            storage="<root>/.peovim/git*/...",
            write_mode="atomic replace",
            coordination="replaceable scratch output",
            guidance="These files are disposable scratch artifacts and may be regenerated or overwritten at any time.",
        ),
    ]
