"""Searching and filtering the lists the GUI shows.

Pure data, no GTK: a filter is a value you can build in a test, and the GUI
only has to turn widgets into one of these and ask each row whether it stays.
Idea borrowed from Cabinet (github.com/Mark12870/cabinet), whose library page
derives a filter record from its search box and dropdowns and rebuilds the list
from it, rather than scattering match conditions through the UI code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

Fields = Callable[[Any], Sequence[str | None]]


@dataclass(frozen=True)
class Filter:
    """Search terms plus any number of exact-match facets.

    `text` matches when *every* whitespace-separated term appears somewhere in
    the row's searchable fields (so "ik bass" finds IK's bass plugin, in either
    order). Facets are exact, case-insensitive, and a facet set to None means
    "any", which is what an unset dropdown gives.
    """
    text: str = ""
    facets: dict[str, str | None] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return bool(self.terms) or any(v is not None for v in self.facets.values())

    @property
    def terms(self) -> tuple[str, ...]:
        """The search words, lower-cased.

        Every term already matches anywhere in the text, so a `*` is redundant
        -- but people type it expecting a wildcard, and a term of literal
        asterisks would then match nothing. Strip them, and drop a term that
        was nothing but wildcards."""
        out = []
        for t in self.text.lower().split():
            t = t.replace("*", "").replace("?", "")
            if t: out.append(t)
        return tuple(out)

    def matches(self, row: Any, fields: Fields, facets: dict[str, Callable[[Any], str | None]] | None = None) -> bool:
        for name, want in self.facets.items():
            if want is None: continue
            get = (facets or {}).get(name)
            if get is None: continue
            got = get(row)
            if got is None or got.lower() != want.lower(): return False
        if not self.terms: return True
        hay = " ".join(str(x) for x in fields(row) if x).lower()
        return all(t in hay for t in self.terms)

    def apply(self, rows: Iterable[Any], fields: Fields,
              facets: dict[str, Callable[[Any], str | None]] | None = None) -> list[Any]:
        return [r for r in rows if self.matches(r, fields, facets)]


def choices(rows: Iterable[Any], get: Callable[[Any], str | None]) -> list[str]:
    """The distinct values of one facet, sorted, for filling a dropdown.

    Case-insensitively unique, keeping the first spelling seen, so a vendor
    writing "FabFilter" and "Fabfilter" does not get two entries."""
    seen: dict[str, str] = {}
    for r in rows:
        v = get(r)
        if v: seen.setdefault(v.lower(), v)
    return [seen[k] for k in sorted(seen)]
