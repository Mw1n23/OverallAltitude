# Contributing

## Development setup
```bash
git clone https://github.com/Mw1n23/OverallAltitude.git
cd OverallAltitude
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[plot]
```

## Before opening a pull request
Run the local gate:
```bash
python -m unittest discover -s tests -q
python -m OverallAltitude --help
```

For a basic runtime smoke test:
```bash
overall-altitude --elevation-source gpx --no-plot
```

## Scope for changes
- Keep the CLI stable unless a change clearly improves usability.
- Prefer deterministic behavior and cached external lookups.
- Treat raw GPX elevation values as fallback data, not as the preferred source of truth.
- Keep edits focused; avoid unrelated refactors in the same pull request.

## Pull request notes
- Describe the GPX or DEM-related behavior you changed.
- Call out any new network dependency or API assumption.
- Include before/after ascent numbers if the change affects analysis output.
