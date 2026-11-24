# Curvis

Curvis is an open-source desktop application for analyzing and plotting scientific data, comparable to GraphPad Prism. You enter or import measurements into data tables, run statistical tests, curve fits and survival analyses, and draw publication-ready graphs, all from one project window and without writing code. It is aimed at lab scientists, runs on Windows, macOS and Linux, and puts correct statistics first: every analysis is validated against reference results from R.

**New to Curvis?** Read [Getting started](docs/getting-started.md), then the [user guide](docs/user-guide/README.md) for every feature.

## Development

Curvis uses Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                          # install
uv run curvis                    # start the app
uv run pytest                    # all tests
uv run pytest tests/validation   # statistics vs. R reference results
uv run pytest tests/bench --benchmark-only
uv run ruff check . && uv run ruff format --check . && uv run mypy src
Rscript validation/r/generate_all.R   # regenerate reference fixtures (needs R)
```

Install the pre-commit hooks once with `uv run pre-commit install`.

### Benchmarks

CI benchmarks each change against its base commit on the same runner: both are run in turn, three times each, and the job fails if any benchmark is more than 15% slower (compared on the minimum time). A failure is confirmed with three more runs of each before the job fails. Shared CI runners differ too much from one another for a stored baseline to gate at 15%.

To compare locally:

```bash
uv run pytest tests/bench --benchmark-only --benchmark-json=base-1.json   # on main
uv run pytest tests/bench --benchmark-only --benchmark-json=head-1.json   # on your branch
uv run python -m tests.bench.compare head-1.json --baseline-runs base-1.json
```

`tests/bench/baseline.json` records absolute timings on a CI runner. CI reports against it without failing. To refresh it, download the `benchmarks` artifact of a CI run and run `uv run python -m tests.bench.compare head-*.json --update`.

### Installers

CI builds unsigned installers on every pull request (the `installers` jobs, defined in `.github/workflows/installers.yml`): an Inno Setup installer for Windows, a `.dmg` for macOS (Apple silicon) and a `.deb` plus an AppImage for Linux. Each one is installed and started on the runner, and the files are uploaded as build artifacts. The Windows installer, the macOS app and the `.deb` register the `.cvp` file type; the AppImage does not.

The app icon is `src/curvis/ui/icons/curvis_dark.svg`; `packaging/make_icons.py` renders the Windows, macOS and Linux icon files from it during the build. The macOS bundle id is `org.curvis.Curvis`.

To build the app bundle locally:

```bash
uv sync --group build
uv run pyinstaller packaging/curvis.spec --noconfirm   # dist/Curvis or dist/Curvis.app
packaging/linux/build-deb.sh 1.0.0                     # Linux: dist/curvis_1.0.0_<arch>.deb
```

### Releases

Pushing a version tag builds the installers (signed on Windows, signed and notarized on macOS), writes the validation report and publishes them as a GitHub release (`.github/workflows/release.yml`); a tag with a hyphen, such as `v1.0.0-rc1`, is a pre-release. The release notes are the version's section of `CHANGELOG.md`. The checklist and the signing secrets are in `docs/releasing.md`.

```bash
git tag -a v1.0.0 -m "Curvis 1.0.0" && git push origin v1.0.0
```

The version in `src/curvis/__init__.py` and `pyproject.toml` (e.g. `1.0.0`, or `1.0.0rc1` for a release candidate) names the installer files. macOS and the Windows file properties show only its numbers (`1.0.0`), and the `.deb` of a pre-release is versioned `1.0.0~rc1` so that `1.0.0` counts as newer.

The validation report (`docs/validation-report.md` and `.pdf`) is written by `uv run python scripts/validation_report.py`; run it again before a release so the committed copy is current.

## License

Curvis is licensed under the GNU General Public License v3.0. See `LICENSE`.
