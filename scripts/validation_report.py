"""Write the validation report: docs/validation-report.md and docs/validation-report.pdf.

    uv run python scripts/validation_report.py            # run tests/validation, write both
    uv run python scripts/validation_report.py --no-pytest  # skip the pytest run

The script runs ``pytest tests/validation`` (every fixture through the statistics
functions and through the analyses as the app runs them), then measures, for every
fixture, the largest difference between Curvis and R on each output, with the same rule
the tests use (tests/validation/harness.py). The report lists each analysis, the R
function and the R and package versions that made its fixtures (from each fixture's
``generated_by``), the number of fixtures, the largest difference per output against
its tolerance, the NIST StRD results and the approved exceptions of
tests/validation/TOLERANCES.md.

It exits with 1 if any test fails or any value is outside its tolerance; the report is
written either way, and says so.
"""

from __future__ import annotations

import argparse
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from tests.validation.adapters import ADAPTERS  # noqa: E402
from tests.validation.harness import (  # noqa: E402
    Difference,
    differences,
    fixture_paths,
    load_fixture,
)
from tests.validation.tolerances import EXCEPTIONS, TOLERANCES  # noqa: E402

MARKDOWN = ROOT / "docs" / "validation-report.md"
PDF = ROOT / "docs" / "validation-report.pdf"
TOLERANCES_MD = ROOT / "tests" / "validation" / "TOLERANCES.md"


@dataclass(frozen=True)
class Reference:
    title: str  # what Curvis computes, as the Results panel names it
    r: str  # the R function(s) the fixtures come from
    group: str


# Every analysis with fixtures, in report order. test_validation_report.py checks that
# this lists exactly the analyses of tests/validation/fixtures.
REFERENCES: dict[str, Reference] = {
    "descriptive_stats": Reference(
        "Descriptive statistics (mean, SD, SEM, 95% CI of the mean)",
        "stats::sd, stats::qt, stats::t.test",
        "Describe",
    ),
    "box_stats": Reference(
        "Box plot five-number summary", "stats::quantile (type = 6)", "Describe"
    ),
    "histogram_bins": Reference(
        "Frequency distribution (histogram bins)",
        "graphics::hist, grDevices::nclass.Sturges",
        "Describe",
    ),
    "violin_density": Reference(
        "Violin plot kernel density", "stats::density, stats::bw.nrd0 (exact sum)", "Describe"
    ),
    "t_test": Reference(
        "Unpaired t test, Welch's t test, paired t test", "stats::t.test", "Compare groups"
    ),
    "mann_whitney": Reference("Mann-Whitney test", "stats::wilcox.test", "Compare groups"),
    "one_way_anova": Reference("Ordinary one-way ANOVA", "stats::aov", "Compare groups"),
    "tukey_hsd": Reference(
        "Tukey's multiple comparisons test", "stats::TukeyHSD", "Compare groups"
    ),
    "dunnett": Reference(
        "Dunnett's multiple comparisons test",
        "multcomp::glht (mcp Dunnett), mvtnorm",
        "Compare groups",
    ),
    "holm_pairwise": Reference(
        "Pairwise t tests, Holm-corrected (after one-way ANOVA)",
        "stats::pairwise.t.test (pool.sd = TRUE), stats::p.adjust (holm)",
        "Compare groups",
    ),
    "rm_anova": Reference(
        "Repeated-measures one-way ANOVA (Greenhouse-Geisser)",
        "stats::aov (Error term), car::Anova (type 3)",
        "Compare groups",
    ),
    "rm_tukey": Reference(
        "Tukey's test after repeated-measures ANOVA",
        "emmeans::emmeans, pairs (adjust = tukey)",
        "Compare groups",
    ),
    "rm_dunnett": Reference(
        "Dunnett's test after repeated-measures ANOVA",
        "multcomp::glht (mcp Dunnett), mvtnorm",
        "Compare groups",
    ),
    "rm_holm": Reference(
        "Holm-adjusted t tests after repeated-measures ANOVA",
        "emmeans::emmeans, pairs (adjust = holm)",
        "Compare groups",
    ),
    "two_way_anova": Reference(
        "Two-way ANOVA (Type III)", "car::Anova (type 3, contr.sum), stats::lm", "Compare groups"
    ),
    "two_way_tukey_a": Reference(
        "Tukey's test on factor A after two-way ANOVA",
        "emmeans::emmeans, pairs (adjust = tukey)",
        "Compare groups",
    ),
    "two_way_tukey_b": Reference(
        "Tukey's test on factor B after two-way ANOVA",
        "emmeans::emmeans, pairs (adjust = tukey)",
        "Compare groups",
    ),
    "kruskal_wallis": Reference("Kruskal-Wallis test", "stats::kruskal.test", "Compare groups"),
    "dunn_all_pairs": Reference(
        "Dunn's multiple comparisons test, all pairs (Holm)",
        "dunn.test::dunn.test, stats::p.adjust (holm)",
        "Compare groups",
    ),
    "dunn_control": Reference(
        "Dunn's multiple comparisons test against a control (Holm)",
        "dunn.test::dunn.test, stats::p.adjust (holm)",
        "Compare groups",
    ),
    "linear_fit": Reference(
        "Linear regression and polynomials (degree 2-6)", "stats::lm", "Fit a curve"
    ),
    "dose_response": Reference(
        "Hill 4PL and 3PL dose-response fits",
        "drc::drm, minpack.lm::nlsLM",
        "Fit a curve",
    ),
    "biphasic": Reference("Biphasic dose-response fit", "minpack.lm::nlsLM", "Fit a curve"),
    "bell_shaped": Reference("Bell-shaped dose-response fit", "minpack.lm::nlsLM", "Fit a curve"),
    "exp_association": Reference(
        "One-phase exponential association fit", "minpack.lm::nlsLM", "Fit a curve"
    ),
    "exp_decay": Reference("One-phase exponential decay fit", "minpack.lm::nlsLM", "Fit a curve"),
    "michaelis_menten": Reference("Michaelis-Menten fit", "minpack.lm::nlsLM", "Fit a curve"),
    "gaussian": Reference("Gaussian fit", "minpack.lm::nlsLM", "Fit a curve"),
    "kaplan_meier": Reference(
        "Kaplan-Meier survival estimate (log-log CI)",
        "survival::survfit (conf.type = log-log)",
        "Survival",
    ),
    "nist_linear": Reference(
        "NIST StRD linear regression", "NIST certified values (read by R)", "NIST StRD"
    ),
    "nist_nls": Reference(
        "NIST StRD nonlinear regression", "NIST certified values (read by R)", "NIST StRD"
    ),
}

GROUPS = ("Describe", "Compare groups", "Fit a curve", "Survival", "NIST StRD")


@dataclass
class OutputSummary:
    kind: str
    tolerance: float
    sources: set[str] = field(default_factory=set)
    values: int = 0
    error: float = 0.0
    ratio: float = 0.0
    worst_case: str = ""
    by_se: bool = False

    def add(self, case: str, d: Difference) -> None:
        self.values += d.values
        self.sources.add(d.source)
        self.tolerance = max(self.tolerance, d.tolerance)
        if d.ratio > self.ratio or (d.ratio == self.ratio and d.error > self.error):
            self.ratio, self.error, self.worst_case, self.by_se = d.ratio, d.error, case, d.by_se


@dataclass
class AnalysisSummary:
    key: str
    script: str = ""
    r_versions: set[str] = field(default_factory=set)
    packages: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    fixtures: int = 0
    expected_failures: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    outputs: dict[str, OutputSummary] = field(default_factory=dict)

    @property
    def values(self) -> int:
        return sum(o.values for o in self.outputs.values())

    @property
    def ratio(self) -> float:
        return max((o.ratio for o in self.outputs.values()), default=0.0)

    @property
    def ok(self) -> bool:
        return not self.errors and self.ratio <= 1.0


@dataclass(frozen=True)
class PytestRun:
    passed: int
    failed: int
    skipped: int
    xfailed: int
    files: dict[str, tuple[int, int]]  # test file -> (passed, not passed)

    @property
    def ok(self) -> bool:
        return self.failed == 0


def measure() -> dict[str, AnalysisSummary]:
    """Runs every fixture through its adapter and keeps the largest difference per
    output."""
    found: dict[str, AnalysisSummary] = {}
    for path in fixture_paths():
        fixture = load_fixture(path)
        summary = found.setdefault(fixture.analysis, AnalysisSummary(fixture.analysis))
        made = fixture.generated_by
        summary.fixtures += 1
        summary.script = made["script"]
        summary.r_versions.add(_short_r_version(made["r_version"]))
        for package, version in made.get("packages", {}).items():
            summary.packages[package].add(version)
        run = ADAPTERS.get(fixture.analysis)
        if run is None:
            summary.errors.append(f"{fixture.case}: no adapter")
            continue
        try:
            actual = run(fixture.inputs)
        except pytest.xfail.Exception as reason:  # a documented expected failure
            summary.expected_failures.append(f"{fixture.case}: {reason}")
            continue
        except Exception as error:
            summary.errors.append(f"{fixture.case}: {type(error).__name__}: {error}")
            continue
        for d in differences(fixture, actual):
            out = summary.outputs.setdefault(d.output, OutputSummary(d.kind, d.tolerance))
            out.add(fixture.case, d)
    return found


def _short_r_version(text: str) -> str:
    match = re.match(r"R version (\S+)", text)
    return match.group(1) if match else text


def run_pytest() -> PytestRun:
    """Runs tests/validation and counts the results per test file."""
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "validation.xml"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/validation",
                "-q",
                "-p",
                "no:cacheprovider",
                f"--junitxml={junit}",
            ],
            cwd=ROOT,
            check=False,
            env={**os.environ, "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen")},
        )
        tree = ET.parse(junit)
    counts = {"passed": 0, "failed": 0, "skipped": 0, "xfailed": 0}
    files: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for case in tree.iter("testcase"):
        name = case.get("classname", "").rsplit(".", 1)[-1] + ".py"
        skipped = case.find("skipped")
        if case.find("failure") is not None or case.find("error") is not None:
            counts["failed"] += 1
            files[name][1] += 1
        elif skipped is not None:
            key = "xfailed" if skipped.get("type") == "pytest.xfail" else "skipped"
            counts[key] += 1
            files[name][1] += 1
        else:
            counts["passed"] += 1
            files[name][0] += 1
    return PytestRun(
        counts["passed"],
        counts["failed"],
        counts["skipped"],
        counts["xfailed"],
        {name: (p, n) for name, (p, n) in sorted(files.items())},
    )


# --- formatting -----------------------------------------------------------------------


def sci(value: float) -> str:
    if value == 0:
        return "0"
    if math.isinf(value):
        return "∞"
    return f"{value:.1e}"


def tolerance_text(out: OutputSummary) -> str:
    if out.kind == "exact" and out.tolerance == 0:
        return "exact"
    text = f"{out.tolerance:g}"
    exceptions = sorted(s for s in out.sources if s.startswith("exception "))
    if exceptions:
        text += " (exception)" if len(out.sources) == 1 else " (exception for some cases)"
    return text


def difference_text(out: OutputSummary) -> str:
    if out.kind == "exact" and out.tolerance == 0:
        return "equal" if out.ratio == 0 else "DIFFERENT"
    text = sci(out.error)
    if out.by_se:
        text += " of SE"
    return text


def status(ok: bool) -> str:
    return "Pass" if ok else "**FAIL**"


def versions(summary: AnalysisSummary) -> str:
    r = ", ".join(sorted(summary.r_versions))
    packages = ", ".join(
        f"{name} {'/'.join(sorted(v))}"
        for name, v in sorted(summary.packages.items())
        if name not in {"jsonlite"}
    )
    return f"R {r}" + (f"; {packages}" if packages else "")


def environment() -> list[tuple[str, str]]:
    import importlib.metadata as md

    import curvis

    def version(name: str) -> str:
        try:
            return md.version(name)
        except md.PackageNotFoundError:
            return "not installed"

    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    return [
        ("Curvis", f"{curvis.__version__}" + (f" (commit {commit})" if commit else "")),
        ("Date", datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")),
        ("Platform", f"{platform.system()} {platform.release()} ({platform.machine()})"),
        ("Python", platform.python_version()),
        (
            "Libraries",
            ", ".join(
                f"{n} {version(n)}" for n in ("numpy", "scipy", "statsmodels", "lmfit", "polars")
            ),
        ),
    ]


def exceptions_section() -> list[str]:
    """The approved rules and exceptions, copied from TOLERANCES.md (everything from its
    first second-level heading on), so the report carries their reasons."""
    text = TOLERANCES_MD.read_text(encoding="utf-8")
    start = text.find("\n## ")
    body = text[start + 1 :] if start >= 0 else text
    return [re.sub(r"^## ", "### ", line) for line in body.rstrip().splitlines()]


def markdown(found: dict[str, AnalysisSummary], tests: PytestRun | None) -> str:
    """The report as Markdown."""
    analyses = [found[k] for k in REFERENCES if k in found]
    total_fixtures = sum(a.fixtures for a in analyses)
    total_values = sum(a.values for a in analyses)
    r_versions = " and ".join(
        f"R {v}" for v in sorted(set().union(*(a.r_versions for a in analyses)))
    )
    ok = all(a.ok for a in analyses) and (tests is None or tests.ok)

    lines = [
        "# Curvis validation report",
        "",
        "Curvis' statistics are checked against reference results computed in R with",
        "established packages, and its curve fits against the certified values of the",
        "NIST Statistical Reference Datasets. This report is generated by",
        "`scripts/validation_report.py` from the committed fixtures in",
        "`tests/validation/fixtures/`; do not edit it by hand.",
        "",
        f"**Result: {'every analysis matches its reference' if ok else 'FAILURES, see below'}.**"
        f" {len(analyses)} analyses, {total_fixtures} fixtures, {total_values:,} values compared.",
        "",
        "| | |",
        "| --- | --- |",
    ]
    env = environment()
    lines += [f"| {k} | {v} |" for k, v in env]

    lines += [
        "",
        "## How values are compared",
        "",
        "Each value is compared by its relative difference, |Curvis − R| / |R|. Tolerances:",
        "",
        "| Kind | Tolerance |",
        "| --- | --- |",
        "| Test statistics | " + f"{TOLERANCES['statistic']:g} |",
        "| P values | " + f"{TOLERANCES['p_value']:g} |",
        "| Fitted parameters | " + f"{TOLERANCES['parameter']:g} |",
        "| Confidence intervals | " + f"{TOLERANCES['ci']:g} |",
        "| Counts, degrees of freedom | exact |",
        "",
        "A fitted parameter smaller than its standard error is compared relative to the",
        'SE instead (shown as "of SE"), and a few outputs have approved exceptions; both',
        "are listed under *Approved rules and exceptions*. NaN must match NaN, and an",
        "infinity the same infinity. *Largest difference* is the largest relative difference",
        "over all fixtures; *share of tolerance* is that difference divided by its tolerance,",
        "so anything up to 100% passes.",
    ]

    if tests is not None:
        lines += [
            "",
            "## Test run",
            "",
            f"`pytest tests/validation`: {tests.passed} passed, {tests.failed} failed,"
            f" {tests.xfailed} expected failures, {tests.skipped} skipped. Besides the",
            "statistics functions, the `test_*_analyses.py` files run each analysis as the",
            "app runs it and compare the numbers the Results panel shows with the same",
            "fixtures.",
            "",
            "| Test file | Passed | Not passed |",
            "| --- | ---: | ---: |",
        ]
        for name, (p, n) in tests.files.items():
            lines.append(f"| `{name}` | {p} | {n} |")

    lines += [
        "",
        "## Summary",
        "",
        f"Every fixture was made with {r_versions}; the package versions are listed per",
        "analysis below.",
        "",
        "| Analysis | R reference | Fixtures | Values | Share of tolerance | Status |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for group in GROUPS:
        for a in analyses:
            ref = REFERENCES[a.key]
            if ref.group != group:
                continue
            share = _share(a.ratio)
            lines.append(
                f"| {ref.title} | `{ref.r}` | {a.fixtures} | {a.values:,} |"
                f" {share} | {status(a.ok)} |"
            )

    for group in GROUPS:
        members = [a for a in analyses if REFERENCES[a.key].group == group]
        if not members:
            continue
        lines += ["", f"## {group}"]
        for a in members:
            ref = REFERENCES[a.key]
            lines += [
                "",
                f"### {ref.title}",
                "",
                f"Fixture set `{a.key}`, {a.fixtures} fixtures from `{a.script}`.",
                f"R reference: `{ref.r}`. Made with {versions(a)}.",
                "",
                "| Output | Kind | Values | Largest difference | Tolerance | Share | Worst case |",
                "| --- | --- | ---: | ---: | ---: | ---: | --- |",
            ]
            for name, out in sorted(a.outputs.items()):
                cells = [
                    name,
                    out.kind,
                    f"{out.values:,}",
                    difference_text(out),
                    tolerance_text(out),
                    _share(out.ratio) if out.tolerance else "-",
                    out.worst_case if out.ratio > 0 else "-",
                ]
                lines.append(
                    f"| `{cells[0]}` | {cells[1]} | {cells[2]} | {cells[3]} | {cells[4]} |"
                    f" {cells[5]} | {cells[6]} |"
                )
            if a.expected_failures:
                lines += [
                    "",
                    "Expected failures (the fit says it did not converge, as it should from",
                    "NIST's distant starting values; from starting values 2 every dataset fits):",
                    "",
                ]
                lines += [f"- {text}" for text in a.expected_failures]
            if a.errors:
                lines += ["", "**Errors:**", ""]
                lines += [f"- {text}" for text in a.errors]

    lines += ["", "## Approved rules and exceptions", ""]
    lines += [
        "Copied from `tests/validation/TOLERANCES.md`, which every exception key in",
        f"`tests/validation/tolerances.py` must appear in ({len(EXCEPTIONS)} exceptions).",
        "",
    ]
    lines += exceptions_section()
    return "\n".join(lines).rstrip() + "\n"


def _share(ratio: float) -> str:
    if ratio == 0:
        return "0%"
    if math.isinf(ratio):
        return "∞"
    percent = 100 * ratio
    if percent < 0.01:
        return "<0.01%"
    return f"{percent:.2g}%" if percent < 10 else f"{percent:.0f}%"


# --- PDF ------------------------------------------------------------------------------


def write_pdf(text: str, path: Path) -> None:
    """Draws the Markdown report as A4 pages with Matplotlib: headings, paragraphs and
    tables in a fixed-width font so the columns line up. Fonts are embedded."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    matplotlib.rcParams.update({"pdf.fonttype": 42, "font.family": "DejaVu Sans"})
    width, height = 8.27, 11.69  # A4 in inches
    margin, top = 0.6, 0.7
    line_height = 0.16  # inches, at 7.5 pt

    blocks = _pdf_blocks(text)
    with PdfPages(path, metadata={"Title": "Curvis validation report"}) as pdf:
        fig = None
        y = 0.0
        page = 0

        def new_page() -> None:
            nonlocal fig, y, page
            if fig is not None:
                pdf.savefig(fig)
                plt.close(fig)
            fig = plt.figure(figsize=(width, height))
            page += 1
            fig.text(
                0.5,
                0.3 / height,
                f"Curvis validation report · page {page}",
                ha="center",
                fontsize=7,
                color="0.4",
            )
            y = height - top

        new_page()
        for style, line in blocks:
            size, mono, weight, gap = {
                "h1": (16, False, "bold", 0.35),
                "h2": (12, False, "bold", 0.28),
                "h3": (9.5, False, "bold", 0.22),
                "p": (8, False, "normal", line_height + 0.02),
                "table": (6.3, True, "normal", line_height - 0.02),
                "blank": (0, False, "normal", 0.08),
            }[style]
            if style in {"h2", "h3"} and y < 1.4:
                new_page()
            if y - gap < 0.6:
                new_page()
            y -= gap
            if style == "blank":
                continue
            assert fig is not None
            fig.text(
                margin / width,
                y / height,
                line,
                fontsize=size,
                fontweight=weight,
                family="DejaVu Sans Mono" if mono else "DejaVu Sans",
                va="baseline",
            )
        assert fig is not None
        pdf.savefig(fig)
        plt.close(fig)


def _pdf_blocks(text: str) -> list[tuple[str, str]]:
    """Markdown to (style, line) pairs: tables become aligned fixed-width rows, long
    paragraphs are wrapped."""
    import textwrap

    out: list[tuple[str, str]] = []
    table: list[list[str]] = []
    paragraph: list[str] = []

    def plain(s: str) -> str:
        return s.replace("**", "").replace("`", "").replace("\\", "")

    def flush_paragraph() -> None:
        if paragraph:
            for line in textwrap.wrap(plain(" ".join(paragraph)), 118):
                out.append(("p", line))
            paragraph.clear()

    def flush_table() -> None:
        if not table:
            return
        rows = [r for r in table if not all(re.fullmatch(r":?-+:?", c) for c in r)]
        limit = 100 if len(rows[0]) <= 2 else 40
        rows = [[_clip(plain(c), limit) for c in r] for r in rows if any(c for c in r)]
        widths = [max(len(r[i]) for r in rows if i < len(r)) for i in range(len(rows[0]))]
        for i, r in enumerate(rows):
            out.append(("table", "  ".join(c.ljust(w) for c, w in zip(r, widths, strict=False))))
            if i == 0:
                out.append(("table", "  ".join("-" * w for w in widths)))
        table.clear()

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("|"):
            flush_paragraph()
            table.append([c.strip() for c in line.strip("|").split("|")])
            continue
        flush_table()
        if not line:
            flush_paragraph()
            out.append(("blank", ""))
        elif line.startswith("#"):
            flush_paragraph()
            level = len(line) - len(line.lstrip("#"))
            out.append((f"h{min(level, 3)}", plain(line.lstrip("# "))))
        elif line.startswith("- "):
            flush_paragraph()
            for i, wrapped in enumerate(textwrap.wrap(plain(line[2:]), 114)):
                out.append(("p", ("• " if i == 0 else "  ") + wrapped))
        else:
            paragraph.append(line)
    flush_paragraph()
    flush_table()
    return out


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-pytest", action="store_true", help="skip running pytest")
    parser.add_argument("--markdown", type=Path, default=MARKDOWN)
    parser.add_argument("--pdf", type=Path, default=PDF)
    args = parser.parse_args(argv)

    np.seterr(all="ignore")
    tests = None if args.no_pytest else run_pytest()
    found = measure()
    missing = set(found) - set(REFERENCES)
    if missing:
        print(f"analyses missing from REFERENCES: {sorted(missing)}", file=sys.stderr)
        return 1
    text = markdown(found, tests)
    args.markdown.write_text(text, encoding="utf-8")
    write_pdf(text, args.pdf)
    ok = all(a.ok for a in found.values()) and (tests is None or tests.ok)
    print(f"wrote {args.markdown} and {args.pdf}: {'all pass' if ok else 'FAILURES'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
