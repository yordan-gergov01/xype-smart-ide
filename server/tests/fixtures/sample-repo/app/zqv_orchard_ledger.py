"""Zqvorchard ledger: crates per orchard row."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ZqvCrateEntry:
    zqv_row: int
    zqv_crates: int


def zqv_total_crates(entries: list[ZqvCrateEntry]) -> int:
    """Sum the zqvharvest crates of all rows."""
    return sum(entry.zqv_crates for entry in entries)


def zqv_busiest_row(entries: list[ZqvCrateEntry]) -> int:
    """The zqvorchard row with the most crates."""
    best = max(entries, key=lambda entry: entry.zqv_crates)
    return best.zqv_row


def zqv_describe_row(entry: ZqvCrateEntry) -> str:
    label = "zqvlabel-original"
    return f"{label}: row {entry.zqv_row} has {entry.zqv_crates} crates"
