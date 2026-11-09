# Developing vstenv

```sh
python3 -m unittest discover -s tests -t .
flatpak/build.sh                     # build and install the Flatpak from the working tree
```

Pushing to `main` auto-tags the next patch release and publishes the bundle; the
push's last commit subject becomes the release note. `scripts/release.sh`
cuts a release with a chosen version and note.

**Reviewing a recipe.** A user's *Share this recipe…* files an issue with the
`recipe` label and a ```json block. Check that the symptom and the setting
make sense (the block can only hold the fixed menu of settings, so there is
nothing dangerous to look for — only whether it is plausible and specific
enough to help others), then add the `approved` label. The
`.github/workflows/recipe.yml` workflow merges the block into
`vstenv/recipes.json` with `scripts/merge-recipe.py` (a second report of the
same recipe adds a confirmation rather than replacing it), commits to `main`
with `[no-release]`, and closes the issue; every app picks the table up at its
next setup pass. An issue without a usable block gets a comment saying what is
missing and loses the label. To merge one by hand: save the issue body to a
file and run `scripts/merge-recipe.py <file>`.

## Layout

- `vstenv/`: the app (CLI in `cli.py`, window in `gui.py`, setup in `setup.py`).
- `vstenv/vendors/`: one module per vendor. `vstenv/vendors/__init__.py`
  defines the interface (see [How it works](how-it-works.md#how-it-works)).
- `vstenv/installers/`: installer engines shared by vendors (InstallAware).
- `patches/wine/`: the Wine patches the app ships, with
  [their own README](../patches/wine/README.md); `scripts/build-wine-fixes.sh`
  builds them, `scripts/build-yabridge.sh` builds yabridge against the pinned Wine.
- `flatpak/`: the Flatpak manifests and `build.sh`.
- `tests/`: unit tests.

## Running from source

See [the command line](command-line.md#running-from-source).
