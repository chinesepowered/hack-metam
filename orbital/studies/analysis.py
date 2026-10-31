"""Grade a measured series against ground truth and flag points that break the physics."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from orbital.truth.usbr import interpolate  # noqa: E402

INK, MUTED, GRID, WATER, TRUTH, BAD = "#e8f1ff", "#8aa3c2", "#1d2b44", "#22d3ee", "#fbbf24", "#f472b6"


def usable_points(points: list[dict]) -> list[dict]:
    return [p for p in points if p.get("usable") and p.get("date")]


def compare_to_elevation(points: list[dict], truth: list[tuple[date, float]]) -> dict:
    """Area must rise with water level. Fit area = f(elevation) (quadratic hypsometry),
    report correlation and scatter, and flag points far off the curve for review."""
    pairs = []
    for p in usable_points(points):
        h = interpolate(truth, date.fromisoformat(p["date"]))
        if h is not None:
            pairs.append((p["date"], h, p["area_km2"]))
    if len(pairs) < 5:
        return {"n": len(pairs), "error": "not enough points with ground truth"}
    dates, h, a = zip(*pairs)
    h, a = np.array(h), np.array(a)
    r = float(np.corrcoef(h, a)[0, 1])
    coef = np.polyfit(h, a, 2)
    resid = a - np.polyval(coef, h)
    rmse = float(np.sqrt(np.mean(resid ** 2)))
    mad = float(np.median(np.abs(resid - np.median(resid))) * 1.4826) or rmse
    outliers = [{"date": d, "area_km2": round(float(x), 2), "elevation_ft": round(float(y), 2),
                 "residual_km2": round(float(e), 2)}
                for d, y, x, e in zip(dates, h, a, resid) if abs(e) > 3 * mad]
    slope = float(np.polyval(np.polyder(coef), h.mean()))
    return {
        "n": len(pairs),
        "pearson_r": round(r, 4),
        "rmse_km2": round(rmse, 2),
        "relative_rmse": round(rmse / float(a.mean()), 4),
        "slope_km2_per_ft": round(slope, 3),
        "fit_coefficients": [float(c) for c in coef],
        "outliers": outliers,
        "pairs": [{"date": d, "elevation_ft": round(float(y), 2), "area_km2": round(float(x), 2)}
                  for d, y, x in zip(dates, h, a)],
    }


def plot_series(points: list[dict], truth: list[tuple[date, float]], grade: dict, title: str, out: Path) -> Path:
    pts = usable_points(points)
    d = [date.fromisoformat(p["date"]) for p in pts]
    area = np.array([p["area_km2"] for p in pts])
    lo = np.array([p["band_km2"][0] for p in pts])
    hi = np.array([p["band_km2"][1] for p in pts])
    tr = [(td, v) for td, v in truth if d and d[0] <= td <= max(d[-1], td if td <= d[-1] else d[-1])]
    flagged = {o["date"] for o in grade.get("outliers", [])}

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig = plt.figure(figsize=(13, 5.2), facecolor="#0b1220")
    ax = fig.add_axes([0.06, 0.13, 0.55, 0.72], facecolor="#0b1220")
    ax2 = ax.twinx()
    ax.fill_between(d, lo, hi, color=WATER, alpha=0.18, lw=0, label="range across methods")
    ax.plot(d, area, color=WATER, lw=2, label="water area (MNDWI)")
    bad = [i for i, p in enumerate(pts) if p["date"] in flagged]
    ax.scatter([d[i] for i in bad], area[bad], color=BAD, zorder=5, s=36, label="flagged for review")
    ax2.plot([t for t, _ in tr], [v for _, v in tr], color=TRUTH, lw=1.6, ls="--", label="USBR elevation")
    ax.set_ylabel("Water surface area (km²)", color=WATER)
    ax2.set_ylabel("Lake elevation, USBR (ft)", color=TRUTH)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    sx = fig.add_axes([0.72, 0.13, 0.25, 0.72], facecolor="#0b1220")
    pairs = grade.get("pairs", [])
    if pairs:
        ph = np.array([p["elevation_ft"] for p in pairs])
        pa = np.array([p["area_km2"] for p in pairs])
        sx.scatter(ph, pa, color=WATER, s=14, alpha=0.85)
        xs = np.linspace(ph.min(), ph.max(), 100)
        sx.plot(xs, np.polyval(grade["fit_coefficients"], xs), color=TRUTH, lw=1.5)
        sx.set_title(f"r = {grade['pearson_r']:.3f} · scatter ±{grade['rmse_km2']:.1f} km²", color=INK, fontsize=11)
    sx.set_xlabel("USBR elevation (ft)")
    sx.set_ylabel("Satellite water area (km²)")

    for a_ in (ax, ax2, sx):
        a_.tick_params(colors=MUTED)
        for s in a_.spines.values():
            s.set_color(GRID)
    for a_ in (ax, sx):
        a_.grid(color=GRID, lw=0.6)
        a_.xaxis.label.set_color(MUTED)
        a_.yaxis.label.set_color(MUTED) if a_ is sx else None
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    leg = ax.legend(h1 + h2, l1 + l2, loc="lower left", fontsize=9, frameon=False)
    for t in leg.get_texts():
        t.set_color(INK)
    fig.suptitle(title, color=INK, fontsize=15, x=0.06, ha="left", y=0.97)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out
