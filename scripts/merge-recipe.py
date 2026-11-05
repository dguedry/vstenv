#!/usr/bin/env python3
"""Merge the recipe in an issue's body into vstenv/recipes.json.

    scripts/merge-recipe.py <issue-body-file> [recipes.json]

The body is what the app's "Share this recipe" prefilled: a ```json block
holding {"programs": {...}} and/or {"plugins": {...}}. The block is cleaned
by the same rules the app applies when it loads the table (unknown kinds,
knobs and values are dropped), then written over the table's entries of the
same name; a 'confirmed' list is appended to rather than replaced, so a
second report of the same recipe adds a confirmation instead of losing one.

Prints one line per entry merged. Exits 2 when the body holds nothing
usable, so the workflow can say so on the issue instead of committing.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vstenv import recipes  # noqa: E402  (the repository's own validation)

BLOCK = re.compile(r"```json\s*\n(.*?)\n\s*```", re.S)


def extract(body: str) -> dict:
    """Every json block's recipes, merged, after the app's cleaning."""
    found = {k: {} for k in recipes.KINDS}
    for m in BLOCK.finditer(body or ""):
        try: raw = json.loads(m.group(1))
        except ValueError: continue
        cleaned = recipes._clean(raw)
        for kind in recipes.KINDS: found[kind].update(cleaned[kind])
    return found


def merge(table: dict, new: dict) -> list[str]:
    """new's entries into table (a loaded recipes.json). Returns what changed."""
    done = []
    for kind in recipes.KINDS:
        table.setdefault(kind, {})
        for name, entry in new.get(kind, {}).items():
            old = table[kind].get(name, {})
            merged = {k: v for k, v in old.items() if k == "confirmed"}
            merged.update({k: v for k, v in entry.items() if k != "confirmed"})
            conf = list(old.get("confirmed", []) if isinstance(old.get("confirmed"), list) else [])
            for c in entry.get("confirmed", []) if isinstance(entry.get("confirmed"), list) else []:
                if c not in conf: conf.append(c)
            if conf: merged["confirmed"] = conf
            table[kind][name] = merged
            settings = ", ".join(f"{k}={v}" for k, v in merged.items() if k in recipes.KNOBS[kind])
            done.append(f"{kind[:-1]} {name}: {settings}")
    return done


def main(argv):
    if len(argv) < 2: sys.exit(__doc__)
    body = Path(argv[1]).read_text(encoding="utf-8", errors="replace")
    path = Path(argv[2]) if len(argv) > 2 else recipes.SHIPPED
    new = extract(body)
    if not any(new[k] for k in recipes.KINDS):
        print("no usable recipe block in the issue body"); return 2
    table = json.loads(path.read_text(encoding="utf-8"))
    for line in merge(table, new): print(line)
    path.write_text(json.dumps(table, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
