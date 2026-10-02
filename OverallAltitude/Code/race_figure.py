"""Comparison figure for the race courses analysed in ``race_comparison``.

One row per course: name, elevation profile (same scale within a section) and total
ascent/descent as bars. Next to the rows, the cumulative ascent of all courses of the
section over the distance. All positions are set in inches from the top-left corner.
"""

from __future__ import annotations

import textwrap
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from .race_comparison import (
    CLIMB_THRESHOLD_M,
    RESAMPLE_DISTANCE_M,
    SMOOTHING_WINDOW,
    CourseProfile,
)

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure


SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
DESCENT_BAR = "#c3c2b7"
DISCIPLINE_COLORS = {
    "marathon": "#2a78d6",
    "triathlon_run": "#eb6834",
    "triathlon_bike": "#1baf7a",
}
DISCIPLINE_LABELS = {
    "marathon": "Marathon",
    "triathlon_run": "Triathlon-Langdistanz: Laufstrecke",
    "triathlon_bike": "Triathlon-Langdistanz: Radstrecke",
}
DISCIPLINE_SHORT_LABELS = {
    "marathon": "Marathon",
    "triathlon_run": "Lauf",
    "triathlon_bike": "Rad",
}
ELEVATION_SOURCE_LABELS = {
    "opentopodata:ned10m": "USGS 3DEP 10 m",
    "terrain-tiles": "Lidar-Geländemodelle über AWS Terrain Tiles",
    "opentopodata:bkg200m": "BKG DGM200",
}

FIGURE_WIDTH_IN = 18.0
MARGIN_IN = 0.6
LABEL_WIDTH_IN = 2.75
SCALE_GUTTER_IN = 0.45
PROFILE_WIDTH_IN = 4.0
BAR_GAP_IN = 0.3
BAR_WIDTH_IN = 2.05
CHART_GAP_IN = 0.75
CHART_AXIS_LABEL_IN = 0.45
CHART_WIDTH_IN = 3.8
CHART_LABEL_GAP_IN = 0.3
ROW_HEIGHT_IN = 0.74
HEADER_HEIGHT_IN = 1.95
SECTION_HEADER_IN = 0.95
AXIS_BAND_IN = 0.5
SECTION_GAP_IN = 0.45
FOOTER_HEIGHT_IN = 2.1
FOOTER_LINE_CHARACTERS = 235
BAR_THICKNESS_PT = 6.5
END_LABEL_SPACING_IN = 0.21
FONT_FAMILIES = ["Inter", "DejaVu Sans"]


def format_number(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".").replace("-", "−")


def format_km(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


def spread_positions(values: Sequence[float], minimum_gap: float, lower: float, upper: float) -> list[float]:
    """Move label positions apart until neighbours keep ``minimum_gap``; order is preserved."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    positions = [values[index] for index in order]
    for _ in range(200):
        moved = False
        for index in range(len(positions) - 1):
            overlap = minimum_gap - (positions[index + 1] - positions[index])
            if overlap > 1e-9:
                positions[index] -= overlap / 2
                positions[index + 1] += overlap / 2
                moved = True
        shift = max(0.0, lower - positions[0])
        positions = [position + shift for position in positions]
        shift = max(0.0, positions[-1] - upper)
        positions = [position - shift for position in positions]
        if not moved:
            break
    result = [0.0] * len(values)
    for rank, index in enumerate(order):
        result[index] = positions[rank]
    return result


def figure_style() -> AbstractContextManager[None]:
    """Font settings; names are resolved when the figure is drawn, so saving needs them too."""
    import matplotlib.pyplot as plt

    return plt.rc_context(
        {"font.family": "sans-serif", "font.sans-serif": FONT_FAMILIES, "svg.fonttype": "path"}
    )


class Layout:
    """Places axes by inches measured from the top-left corner of the figure."""

    def __init__(self, figure: Figure, width_in: float, height_in: float):
        self.figure = figure
        self.width_in = width_in
        self.height_in = height_in

    def axes(self, left_in: float, top_in: float, width_in: float, height_in: float) -> Axes:
        axes = self.figure.add_axes(
            (
                left_in / self.width_in,
                1.0 - (top_in + height_in) / self.height_in,
                width_in / self.width_in,
                height_in / self.height_in,
            )
        )
        axes.set_facecolor("none")
        for spine in axes.spines.values():
            spine.set_visible(False)
        axes.tick_params(length=0)
        return axes

    def text(self, left_in: float, top_in: float, content: str, **style) -> None:
        self.figure.text(
            left_in / self.width_in,
            1.0 - top_in / self.height_in,
            content,
            va=style.pop("va", "top"),
            ha=style.pop("ha", "left"),
            **style,
        )


def draw_bar(axes: Axes, length: float, row: float, color: str, data_per_point: float) -> None:
    """Thin bar that is square at the baseline and rounded at the data end.

    The round cap at the baseline lies left of the axes and is clipped away.
    """
    tip = max(length - BAR_THICKNESS_PT / 2 * data_per_point, 0.0)
    axes.plot([0, tip], [row, row], color=color, linewidth=BAR_THICKNESS_PT, solid_capstyle="round")


def draw_section(
    layout: Layout,
    profiles: Sequence[CourseProfile],
    top_in: float,
    title: str,
    subtitle: str,
    profile_scale_m: float,
    profile_ticks_km: Sequence[float],
    chart_step_m: float,
) -> float:
    """Draw one discipline section and return the y position (inches) below it."""
    rows = sorted(profiles, key=lambda profile: profile.total_ascent_m, reverse=True)
    row_count = len(rows)
    rows_top_in = top_in + SECTION_HEADER_IN
    rows_height_in = row_count * ROW_HEIGHT_IN
    label_left_in = MARGIN_IN
    profile_left_in = label_left_in + LABEL_WIDTH_IN + SCALE_GUTTER_IN
    bar_left_in = profile_left_in + PROFILE_WIDTH_IN + BAR_GAP_IN
    chart_left_in = bar_left_in + BAR_WIDTH_IN + CHART_GAP_IN
    table_right_in = bar_left_in + BAR_WIDTH_IN
    max_distance_km = max(profile.distance_km for profile in rows)

    layout.text(label_left_in, top_in, title, fontsize=15, fontweight="bold", color=INK)
    layout.text(label_left_in, top_in + 0.33, subtitle, fontsize=10, color=INK_SECONDARY)
    caption_top_in = rows_top_in - 0.27
    layout.text(
        profile_left_in,
        caption_top_in,
        "Höhenprofil über dem tiefsten Punkt der Strecke",
        fontsize=9.5,
        color=INK_MUTED,
    )
    layout.text(
        bar_left_in,
        caption_top_in,
        "Höhenmeter gesamt: ↑ Anstieg, ↓ Abstieg",
        fontsize=9.5,
        color=INK_MUTED,
    )
    layout.text(
        chart_left_in + CHART_AXIS_LABEL_IN,
        caption_top_in,
        "Aufsummierter Anstieg vom Start bis zum jeweiligen Kilometer",
        fontsize=9.5,
        color=INK_MUTED,
    )

    # Row separators across the table part.
    rules = layout.axes(label_left_in, rows_top_in, table_right_in - label_left_in, rows_height_in)
    rules.set_xlim(0, 1)
    rules.set_ylim(row_count, 0)
    rules.set_xticks([])
    rules.set_yticks([])
    for row in range(row_count + 1):
        rules.axhline(row, color=GRID, linewidth=0.8)

    # Vertical distance guides behind all profile rows.
    guides = layout.axes(profile_left_in, rows_top_in, PROFILE_WIDTH_IN, rows_height_in)
    guides.set_xlim(0, max_distance_km)
    guides.set_ylim(0, 1)
    guides.set_yticks([])
    guides.set_xticks(list(profile_ticks_km))
    guides.set_xticklabels([format_km(tick, 0) for tick in profile_ticks_km], fontsize=9, color=INK_MUTED)
    guides.tick_params(axis="x", pad=6)
    for tick in profile_ticks_km:
        guides.axvline(tick, color=GRID, linewidth=0.8)
    guides.set_xlabel("Streckenkilometer", fontsize=9, color=INK_MUTED, labelpad=3)

    profile_height_in = ROW_HEIGHT_IN - 0.14
    for row, profile in enumerate(rows):
        row_top_in = rows_top_in + row * ROW_HEIGHT_IN
        color = DISCIPLINE_COLORS[profile.spec.discipline]
        spec = profile.spec

        layout.text(
            label_left_in,
            row_top_in + 0.15,
            spec.event + (" *" if spec.coarse_terrain_model else ""),
            fontsize=12,
            fontweight="bold",
            color=INK,
        )
        details = [spec.location.split(",")[0]]
        if spec.laps > 1:
            details.append(f"{spec.laps} Runden")
        details.append(
            f"{format_number(profile.min_elevation_m)}–{format_number(profile.max_elevation_m)} m ü. M."
        )
        layout.text(
            label_left_in,
            row_top_in + 0.41,
            " · ".join(details),
            fontsize=9.5,
            color=INK_SECONDARY,
        )

        axes = layout.axes(profile_left_in, row_top_in + 0.09, PROFILE_WIDTH_IN, profile_height_in)
        relative_m = [elevation - profile.min_elevation_m for elevation in profile.elevations_m]
        axes.fill_between(profile.distances_km, relative_m, 0, color=color, alpha=0.18, linewidth=0)
        axes.plot(profile.distances_km, relative_m, color=color, linewidth=1.4, solid_joinstyle="round")
        axes.axhline(0, color=BASELINE, linewidth=0.8)
        axes.set_xlim(0, max_distance_km)
        axes.set_ylim(0, profile_scale_m)
        axes.set_xticks([])
        axes.set_yticks([])

        if row == 0:
            # Height scale for the whole section, drawn in the gutter left of the first profile.
            km_per_inch = max_distance_km / PROFILE_WIDTH_IN
            scale_x = -0.14 * km_per_inch
            scale_m = 100.0
            axes.plot(
                [scale_x, scale_x],
                [0, scale_m],
                color=INK_SECONDARY,
                linewidth=1.0,
                clip_on=False,
                solid_capstyle="butt",
            )
            for tick_m in (0, scale_m):
                axes.plot(
                    [scale_x - 0.035 * km_per_inch, scale_x + 0.035 * km_per_inch],
                    [tick_m, tick_m],
                    color=INK_SECONDARY,
                    linewidth=1.0,
                    clip_on=False,
                )
            axes.text(
                scale_x - 0.07 * km_per_inch,
                scale_m / 2,
                "100 m",
                rotation=90,
                va="center",
                ha="right",
                fontsize=8.5,
                color=INK_SECONDARY,
                clip_on=False,
            )

    # Ascent and descent bars, one pair per row on a common scale.
    longest_m = max(max(profile.total_ascent_m, profile.total_descent_m) for profile in rows)
    bar_scale_m = longest_m / 0.62
    bars = layout.axes(bar_left_in, rows_top_in, BAR_WIDTH_IN, rows_height_in)
    bars.set_xlim(0, bar_scale_m)
    bars.set_ylim(row_count, 0)
    bars.set_xticks([])
    bars.set_yticks([])
    bars.axvline(0, color=BASELINE, linewidth=0.8)
    data_per_point = bar_scale_m / (BAR_WIDTH_IN * 72)
    label_offset_m = 6 * data_per_point
    for row, profile in enumerate(rows):
        color = DISCIPLINE_COLORS[profile.spec.discipline]
        for offset, value, bar_color, arrow in (
            (0.34, profile.total_ascent_m, color, "↑"),
            (0.66, profile.total_descent_m, DESCENT_BAR, "↓"),
        ):
            draw_bar(bars, value, row + offset, bar_color, data_per_point)
            bars.text(
                value + label_offset_m,
                row + offset,
                f"{arrow} {format_number(value)} m",
                va="center",
                ha="left",
                fontsize=10,
                fontweight="bold" if arrow == "↑" else "normal",
                color=INK if arrow == "↑" else INK_SECONDARY,
            )

    # Cumulative ascent over the distance for all courses of the section.
    chart = layout.axes(
        chart_left_in + CHART_AXIS_LABEL_IN,
        rows_top_in + 0.1,
        CHART_WIDTH_IN,
        rows_height_in - 0.1,
    )
    highest_m = max(profile.total_ascent_m for profile in rows)
    chart_top_m = chart_step_m * (int(highest_m // chart_step_m) + 1)
    chart.set_xlim(0, max_distance_km)
    chart.set_ylim(0, chart_top_m)
    chart.set_xticks(list(profile_ticks_km))
    chart.set_xticklabels([format_km(tick, 0) for tick in profile_ticks_km], fontsize=9, color=INK_MUTED)
    y_ticks = [chart_step_m * step for step in range(int(chart_top_m // chart_step_m) + 1)]
    chart.set_yticks(y_ticks)
    chart.set_yticklabels([f"{format_number(tick)} m" for tick in y_ticks], fontsize=9, color=INK_MUTED)
    chart.tick_params(axis="x", pad=6)
    chart.tick_params(axis="y", pad=6)
    chart.set_xlabel("Streckenkilometer", fontsize=9, color=INK_MUTED, labelpad=3)
    for tick in y_ticks:
        chart.axhline(tick, color=BASELINE if tick == 0 else GRID, linewidth=0.8, zorder=0)

    chart_height_in = rows_height_in - 0.1
    label_positions = spread_positions(
        [profile.total_ascent_m for profile in rows],
        minimum_gap=END_LABEL_SPACING_IN / chart_height_in * chart_top_m,
        lower=0.0,
        upper=chart_top_m,
    )
    km_per_inch = max_distance_km / CHART_WIDTH_IN
    label_km = max_distance_km + CHART_LABEL_GAP_IN * km_per_inch
    for profile, label_m in zip(rows, label_positions):
        color = DISCIPLINE_COLORS[profile.spec.discipline]
        end_km = profile.distances_km[-1]
        end_m = profile.cumulative_ascent_m[-1]
        chart.plot(
            profile.distances_km,
            profile.cumulative_ascent_m,
            color=color,
            linewidth=1.6,
            solid_joinstyle="round",
            solid_capstyle="round",
            zorder=3,
        )
        chart.plot(
            [end_km + 0.06 * km_per_inch, label_km - 0.05 * km_per_inch],
            [end_m, label_m],
            color=BASELINE,
            linewidth=0.8,
            clip_on=False,
            zorder=2,
        )
        chart.plot(
            [end_km],
            [end_m],
            marker="o",
            markersize=6.5,
            markerfacecolor=color,
            markeredgecolor=SURFACE,
            markeredgewidth=1.4,
            clip_on=False,
            zorder=4,
        )
        chart.text(
            label_km,
            label_m,
            f"{profile.spec.event}  {format_number(end_m)} m",
            va="center",
            ha="left",
            fontsize=9.5,
            color=INK,
            clip_on=False,
        )

    return rows_top_in + rows_height_in + AXIS_BAND_IN


def footer_paragraphs(profiles: Sequence[CourseProfile]) -> list[str]:
    """Method, caveats and track sources, derived from the analysed courses."""
    specs = [profile.spec for profile in profiles]
    terrain_models = [
        label
        for source, label in ELEVATION_SOURCE_LABELS.items()
        if any(spec.elevation_source == source for spec in specs)
    ]
    smoothing_m = int(SMOOTHING_WINDOW * RESAMPLE_DISTANCE_M)
    threshold = format_km(CLIMB_THRESHOLD_M, 0)
    method = (
        f"Methode: Jede Strecke wird alle {int(RESAMPLE_DISTANCE_M)} m abgetastet. Die Höhen stammen "
        "nicht aus dem GPS-Track, sondern aus dem besten frei verfügbaren Geländemodell der Region "
        f"({'; '.join(terrain_models)}). Brücken und Tunnel sind korrigiert (OpenStreetMap; New Yorker "
        f"Brücken mit Vermessungspunkten von NYC Open Data). Das Profil ist über {smoothing_m} m "
        "geglättet; gezählt werden nur An- und Abstiege zwischen Richtungswechseln von mindestens "
        f"{threshold} m."
    )
    context = (
        "Einordnung: Höhenmeter-Summen hängen von Geländemodell, Glättung und Schwellenwert ab. Sie "
        "sind hier für alle Strecken gleich bestimmt und damit untereinander vergleichbar; "
        "Veranstalterangaben können abweichen. Laufstrecken sind auf 42,195 km normiert."
    )
    if any(spec.coarse_terrain_model for spec in specs):
        context += (
            " * Nur ein 200-m-Geländemodell frei verfügbar; diese Werte sind weniger genau und "
            "tendenziell etwas zu hoch."
        )

    by_source: dict[str, dict[str, list[str]]] = {}
    for spec in specs:
        by_source.setdefault(spec.source_name, {}).setdefault(spec.event, []).append(
            DISCIPLINE_SHORT_LABELS[spec.discipline]
            if spec.discipline != "marathon"
            else spec.course_version
        )
    sources = []
    for source_name, events in by_source.items():
        items = [f"{event} ({', '.join(details)})" for event, details in events.items()]
        sources.append(f"{source_name}: {', '.join(items)}")
    tracks = "Strecken (GPX): " + "; ".join(sources) + "."
    lapped = [
        f"{spec.event} {DISCIPLINE_SHORT_LABELS[spec.discipline]} ({spec.laps} Runden)"
        for spec in specs
        if spec.laps > 1
    ]
    if lapped:
        tracks += " Eine Runde mehrfach aneinandergereiht: " + ", ".join(lapped) + "."
    return [method, context, tracks]


def build_comparison_figure(profiles: Sequence[CourseProfile]) -> Figure:
    import matplotlib.pyplot as plt

    run_profiles = [profile for profile in profiles if profile.spec.discipline != "triathlon_bike"]
    bike_profiles = [profile for profile in profiles if profile.spec.discipline == "triathlon_bike"]
    if not run_profiles or not bike_profiles:
        raise ValueError("The figure needs at least one run course and one bike course.")

    height_in = (
        HEADER_HEIGHT_IN
        + 2 * (SECTION_HEADER_IN + AXIS_BAND_IN)
        + (len(run_profiles) + len(bike_profiles)) * ROW_HEIGHT_IN
        + SECTION_GAP_IN
        + FOOTER_HEIGHT_IN
    )
    with figure_style():
        figure = plt.figure(figsize=(FIGURE_WIDTH_IN, height_in), facecolor=SURFACE)
        layout = Layout(figure, FIGURE_WIDTH_IN, height_in)

        marathon_count = sum(1 for profile in profiles if profile.spec.discipline == "marathon")
        triathlon_count = len(bike_profiles)
        layout.text(
            MARGIN_IN,
            0.5,
            f"Höhenmeter im Vergleich: {marathon_count} Marathons und "
            f"{triathlon_count} Triathlon-Langdistanzen",
            fontsize=25,
            fontweight="bold",
            color=INK,
        )
        layout.text(
            MARGIN_IN,
            1.02,
            "Höhenprofil, Gesamtanstieg und Gesamtabstieg jeder Strecke, für alle Strecken "
            "mit derselben Methode aus Geländemodellen berechnet",
            fontsize=13,
            color=INK_SECONDARY,
        )
        legend_left_in = MARGIN_IN
        for discipline in ("marathon", "triathlon_run", "triathlon_bike"):
            swatch = layout.axes(legend_left_in, 1.52, 0.3, 0.14)
            swatch.set_xlim(0, 1)
            swatch.set_ylim(0, 1)
            swatch.set_xticks([])
            swatch.set_yticks([])
            swatch.fill_between([0, 1], [1, 1], 0, color=DISCIPLINE_COLORS[discipline], alpha=0.18, linewidth=0)
            swatch.plot([0, 1], [1, 1], color=DISCIPLINE_COLORS[discipline], linewidth=2.2, solid_capstyle="butt")
            label = DISCIPLINE_LABELS[discipline]
            layout.text(legend_left_in + 0.4, 1.5, label, fontsize=11, color=INK)
            legend_left_in += 0.4 + 0.08 * len(label) + 0.45

        run_distance = format_km(max(profile.distance_km for profile in run_profiles), 3)
        next_top_in = draw_section(
            layout,
            run_profiles,
            top_in=HEADER_HEIGHT_IN,
            title=f"Laufstrecken über die Marathondistanz ({run_distance} km)",
            subtitle="Sortiert nach Gesamtanstieg. Alle Profile im selben Höhenmaßstab.",
            profile_scale_m=150.0,
            profile_ticks_km=(0, 10, 20, 30, 40),
            chart_step_m=100.0,
        )
        next_top_in = draw_section(
            layout,
            bike_profiles,
            top_in=next_top_in + SECTION_GAP_IN,
            title="Radstrecken der Triathlon-Langdistanz (rund 180 km)",
            subtitle=(
                "Sortiert nach Gesamtanstieg. Eigener Maßstab: Strecke und Höhe sind anders "
                "skaliert als bei den Laufstrecken."
            ),
            profile_scale_m=260.0,
            profile_ticks_km=(0, 30, 60, 90, 120, 150, 180),
            chart_step_m=500.0,
        )

        paragraphs = footer_paragraphs(profiles)
        footer_top_in = height_in - FOOTER_HEIGHT_IN + 0.25
        rule = layout.axes(MARGIN_IN, footer_top_in - 0.18, FIGURE_WIDTH_IN - 2 * MARGIN_IN, 0.02)
        rule.set_xticks([])
        rule.set_yticks([])
        rule.axhline(0.5, color=BASELINE, linewidth=0.8)
        line_top_in = footer_top_in
        for paragraph in paragraphs:
            for line in textwrap.wrap(paragraph, FOOTER_LINE_CHARACTERS):
                layout.text(MARGIN_IN, line_top_in, line, fontsize=9.5, color=INK_SECONDARY)
                line_top_in += 0.21
            line_top_in += 0.07
    return figure


def save_comparison_figure(profiles: Sequence[CourseProfile], output_dir: Path) -> list[Path]:
    import matplotlib.pyplot as plt

    figure = build_comparison_figure(profiles)
    output_dir.mkdir(parents=True, exist_ok=True)
    files = [output_dir / "race_comparison.png", output_dir / "race_comparison.svg"]
    with figure_style():
        figure.savefig(files[0], dpi=160, facecolor=SURFACE)
        figure.savefig(files[1], facecolor=SURFACE)
    plt.close(figure)
    return files
