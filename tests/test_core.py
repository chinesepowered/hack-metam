"""Fast checks of the institute's logic, no network or model calls."""
import numpy as np
import pytest
import xarray as xr

from orbital import sops
from orbital.agents.audit import audit_finding
from orbital.agents.runtime import _cap_images
from orbital.instruments.burn import FireSOP, measure_burn
from orbital.instruments.water import WaterSOP, keep_water_bodies, measure_water


def test_audit_traces_rounded_values_and_flags_invented_ones():
    results = [{"tool": "measure", "result": {"latest": {"area_km2": 251.8816}, "r": 0.2685}}]
    finding = {"claims": [
        {"statement": "latest area", "value": 251.9},   # rounding of a tool value
        {"statement": "correlation", "value": 0.27},    # rounding of 0.2685
        {"statement": "made up", "value": 412.0},       # appears nowhere
    ]}
    status = [c["status"] for c in audit_finding(finding, results)]
    assert status == ["traced", "traced", "untraced"]


def test_sop_issue_versions_types_and_rejects_unknown_fields():
    sops.reset()
    entry = sops.issue("water", {"dark_water_rule": "true", "dark_reflectance": "0.025"}, "dark water")
    assert entry["version"] == "water-sop-2"
    cur = sops.current("water")
    assert cur.dark_water_rule is True and cur.dark_reflectance == 0.025
    with pytest.raises(ValueError):
        sops.issue("water", {"not_a_field": 1}, "nope")
    sops.reset()
    assert sops.current("water").version == "water-sop-1"


def test_sop_key_changes_with_parameters_not_just_version():
    a = WaterSOP(version="water-sop-2", dark_water_rule=True, dark_reflectance=0.025)
    b = WaterSOP(version="water-sop-2", dark_water_rule=True, dark_reflectance=0.015)
    assert a.key() != b.key()
    assert a.key().startswith("water-sop-2-")


def test_gates_know_their_direction():
    base = WaterSOP(dark_water_rule=True, dark_reflectance=0.025)
    gated = WaterSOP(dark_water_rule=True, dark_reflectance=0.025, max_dark_share=0.5, min_method_iou=0.8)
    assert gated.at_least_as_strict_as(base) and not base.at_least_as_strict_as(gated)
    assert base.key().endswith(WaterSOP(dark_water_rule=True, dark_reflectance=0.025).key()[-6:])
    point = {"cloud_near_water": 0.0, "nodata_fraction": 0.0, "dark_share": 0.2, "agreement_iou": {"esa_scl": 0.79},
             "band_km2": [250, 320], "area_km2": 260}
    assert base.passes(point) and not gated.passes(point)  # IoU 0.79 < 0.8
    assert gated.measure_params() == base.measure_params()  # gates don't change what a scene measures


def test_image_cap_keeps_most_recent_images():
    img = {"type": "image_url", "image_url": {"url": "data:"}}
    messages = [{"role": "user", "content": [dict(img) for _ in range(5)]} for _ in range(3)]
    _cap_images(messages, limit=8)
    kept = [p for m in messages for p in m["content"] if p["type"] == "image_url"]
    assert len(kept) == 8
    assert messages[0]["content"][0]["type"] == "text"  # the oldest go first


def test_keep_water_bodies_drops_small_ponds():
    water = np.zeros((50, 50), bool)
    water[5:30, 5:30] = True  # lake: 625 px
    water[40:42, 40:42] = True  # pond: 4 px
    kept = keep_water_bodies(water, pixel_area_m2=1600, min_km2=0.1)
    assert kept[10, 10] and not kept[41, 41]


def _scene(green, nir, swir16, swir22, scl, date="2026-01-01"):
    shape = green.shape
    red = blue = np.full(shape, 0.05, np.float32)
    return xr.Dataset({k: (("y", "x"), v) for k, v in dict(
        green=green, nir=nir, swir16=swir16, swir22=swir22, red=red, blue=blue, scl=scl).items()},
        attrs={"date": date, "pixel_area_m2": 1600.0})


def test_dark_water_rule_recovers_over_corrected_lake():
    shape = (60, 60)
    land = dict(green=np.full(shape, .15, np.float32), nir=np.full(shape, .25, np.float32),
                swir16=np.full(shape, .3, np.float32), swir22=np.full(shape, .25, np.float32))
    lake = np.zeros(shape, bool)
    lake[10:50, 10:50] = True
    for band in land.values():
        band[lake] = 0.004  # near-black in every band: MNDWI is noise here
    land["swir16"][lake] = 0.006
    scl = np.full(shape, 5, np.uint8)
    scl[lake] = 6
    ds = _scene(**land, scl=scl)
    v1 = measure_water(ds, WaterSOP())
    v2 = measure_water(ds, WaterSOP(dark_water_rule=True, dark_reflectance=0.025))
    assert v1.area_km2 < 0.5 * v2.area_km2
    assert v2.agreement_iou["esa_scl"] > 0.95
    assert v1.warnings  # the instrument flags the problem even when the rule is off


def test_burn_offset_and_domain_remove_false_burns_outside_perimeter():
    shape = (200, 200)
    perim = np.zeros(shape, bool)
    perim[60:140, 60:140] = True
    pre = _scene(np.full(shape, .08, np.float32), np.full(shape, .35, np.float32), np.full(shape, .2, np.float32),
                 np.full(shape, .12, np.float32), np.full(shape, 4, np.uint8))
    post_nir = np.full(shape, .31, np.float32)  # mild seasonal drying everywhere
    post_nir[perim] = .12                       # the burn
    post_nir[170:190, 10:40] = .2               # a harvested field far from the fire
    post = _scene(np.full(shape, .08, np.float32), post_nir, np.full(shape, .2, np.float32),
                  np.full(shape, .2, np.float32), np.full(shape, 4, np.uint8))
    v1 = measure_burn(pre, post, perim, official_km2=perim.sum() * 0.0016, sop=FireSOP())
    v2 = measure_burn(pre, post, perim, official_km2=perim.sum() * 0.0016,
                      sop=FireSOP(analysis_buffer_km=1.0, phenology_offset=True))
    assert v1.burned_outside_perimeter_km2 > 0.5
    assert v2.burned_outside_perimeter_km2 < 0.05
    assert v2.iou > v1.iou
