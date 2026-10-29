# Curvis

Curvis is an open-source desktop application for analyzing and plotting scientific data, comparable to GraphPad Prism. You enter or import measurements into data tables, run statistical tests, curve fits and survival analyses, and draw publication-ready graphs, all from one project window and without writing code. It is aimed at lab scientists, runs on Windows, macOS and Linux, and puts correct statistics first: every analysis is validated against reference results from R.

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

CI runs the benchmarks in `tests/bench` three times and fails if any is more than 15% slower than the stored baseline, `tests/bench/baseline.json`. Timings are compared on their minimum and scaled by a calibration benchmark, so the baseline can come from a different machine. To check locally:

```bash
uv run pytest tests/bench --benchmark-only --benchmark-json=bench.json
uv run python -m tests.bench.compare bench.json
```

To update the baseline after an intended change, download the `benchmarks` artifact from a CI run (or make three runs locally) and store it:

```bash
uv run python -m tests.bench.compare bench-1.json bench-2.json bench-3.json --update
```

See `docs/SPEC.md` for the product spec and `docs/PHASES.md` for the build plan.

## License

Curvis is licensed under the GNU General Public License v3.0. See `LICENSE`.
