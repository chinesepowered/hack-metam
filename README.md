<p align="center"><img src="web/assets/capy-astronaut.svg" width="140" alt="Capy, Orbital's mascot"></p>

# Orbital

**A one-person Earth-science institute, run by AI labs on Meta's Muse models.**

Orbital turns free satellite imagery into *verified* measurements of the planet. A Director agent plans studies,
lab agents measure lakes and wildfires from Sentinel-2, a Reviewer signs off on every finding, and a deterministic
Auditor traces every number back to the tool that produced it. When the institute finds a flaw in its own methods,
the Director issues a new standard operating procedure (SOP), the labs re-measure, and the bulletin says what was
caught.

Built for the **Meta Global AI Developer Hackathon** (problem statement: *Push the Frontier*). Everything in this
repository was written during the hackathon; the repository started empty (see the commit history).

<p align="center">
  <img src="web/assets/capy-director.svg" width="90" alt="Director">
  <img src="web/assets/capy-water.svg" width="90" alt="Water Lab">
  <img src="web/assets/capy-fire.svg" width="90" alt="Fire Lab">
  <img src="web/assets/capy-reviewer.svg" width="90" alt="Reviewer">
  <img src="web/assets/capy-auditor.svg" width="90" alt="Auditor">
</p>

## What one research cycle looks like

1. **Director** reads a briefing and assigns one focused study per question to a lab.
2. **Labs** measure with real instruments (tools), all on free data with no keys:
   - **Water Lab** — monthly lake surface area from Sentinel-2 (MNDWI, cross-checked against ESA's scene
     classification and a locally fitted threshold), graded against the U.S. Bureau of Reclamation's gauge.
   - **Fire Lab** — burn scar and severity from the change in Normalized Burn Ratio between clear pre- and
     post-fire scenes, graded against the official NIFC fire perimeter.
3. **Auditor** (plain code, no model) checks that every value a lab claims appears in that lab's own tool outputs.
4. **Reviewer** verifies each claim, *watches* the whole measured series as a satellite time-lapse (video
   understanding), inspects every flagged frame, re-measures key numbers from raw imagery, and applies the
   institute's publication standards (e.g. lake area must track the gauge at r ≥ 0.8; a burn map must overlap the
   perimeter at IoU ≥ 0.75).
5. **Director** rules: publish, or fix the procedure from evidence (a methods study, a versioned SOP that every lab
   follows) and send the study back.
6. A **bulletin** is published with the findings and an error ledger of what the institute caught in its own work.

## Results (cycle-5, a full autonomous run)

Measured by the labs on real data. Under SOP v1 the Reviewer sent both studies back; the Director ran a methods
study for each lab, issued SOP v2, and both re-measured studies passed review in round 2.

| Study | SOP v1 | SOP v2 (issued by the Director) |
|---|---|---|
| Lake Mead surface area vs USBR gauge, 2018–2026 | Pearson r **0.27**, scatter ±49.6 km² (73 months) | Pearson r **0.99**, scatter ±2.9 km² (30 months) |
| Tartar Fire (Idaho, 2026) burn map vs official perimeter | IoU **0.66**, precision 0.69, 267 km² outside | IoU **0.89**, precision 0.96, 26 km² outside |

- **What went wrong in v1, water:** in low-sun winter scenes the atmospheric correction drives Lake Mead's
  reflectance to near zero in every band. The water index turns to noise and most of the lake is missed
  (5.6 km² measured on 2025-02-18); the evidence images show the lake rendered black. The water methods study showed
  a dark-water rule restores agreement with the gauge, and its quality-gate table showed what stricter scene
  selection costs in months and gains in agreement. The Director chose the dark-water rule plus two gates (at most
  half the lake near-black; the outline must overlap ESA's own water classification at IoU ≥ 0.8). The trade-off is
  honest and stated in the bulletin: 30 of 106 months pass, with sparse coverage in 2022–2023.
- **What went wrong in v1, fire:** crop harvest, seasonal drying and land across the Snake River were mapped as
  burned. The fire methods study scored 12 procedure variants against the perimeter in seconds; counting only
  within 1 km of the incident's perimeter scored best (IoU 0.89, 97% of the official area mapped as burned).
- **Context the Water Lab reported:** USBR's end-of-month record shows Lake Mead at 1,037.8 ft on 2026-09-30,
  its lowest since 1940, below the July 2022 low of 1,040.92 ft.
- **Earlier cycles** each exposed a flaw that is now fixed in the code: a reviewer that ran out of steps, a
  Director that sent work back without changing the procedure (now refused by a guard), a quality gate that
  measured the wrong thing, and a stale settings file that crashed re-measurement (the lab reported the failure
  instead of inventing numbers).

A static, key-free replay of cycle-5: [hack-metam-static](https://github.com/chinesepowered/hack-metam-static).

## Meta models

| Role | Model | How it is used |
|---|---|---|
| Director, labs, reviewer | **Muse Spark Open** (`meta-models/muse-spark-open-1026`, open weights, served by Baseten) | Tool calling, multi-step agent loops, vision over satellite evidence images, **video understanding** (skimming 8 years of monthly time-lapse in parallel clips to find the months an instrument got wrong), reasoning |
| Director / reviewer, optional | Muse Wonder / Muse Spark 1.3 (Meta Model API) | Opt in with `ORBITAL_META_ROLES=1` and `MODEL_API_KEY` (see `orbital.institute.roles()`) |

Structured results come back through tool calls rather than `response_format=json_schema`, which degenerates into
whitespace on the Baseten deployment. Evidence images are capped at 8 per request, the deployment's limit.

**Video review, measured.** `watch_timelapse` turns the measured series into clips of 24 months at 2 s per frame
(the model samples about one frame per second) and reviews them in parallel. On the flawed SOP v1 series (73
months) it flagged all three dark-water failures with exact dates (2020-10-22, 2021-01-30, 2025-02-18) plus seven
narrow-channel gaps in ~27 s; on the gated SOP v2 series it flagged nothing. Flags are matched to real data points by
date and printed area, and the Reviewer confirms each one with `view_evidence`.

## Run it

```bash
uv sync
```

Set `BASETEN_API_KEY` (required) and optionally `MODEL_API_KEY`. Then start mission control and press
**Run a new cycle**:

```bash
uv run uvicorn orbital.web:app --port 8000
```

Or run a cycle from the command line:

```bash
uv run python -m orbital.institute
```

Baselines and methods work, no model keys needed:

```bash
uv run python scripts/lake_mead.py 2
```

```bash
uv run python scripts/water_methods_eval.py runs/lake-mead/water-sop-1-764ec8/series.json
```

Tests (no network or model calls):

```bash
uv run pytest
```

Demo video pipeline: `scripts/demo/` (narration text generated from the run's outputs, AssemblyAI text-to-speech,
Playwright screen capture, ffmpeg).

## Layout

```
orbital/
  imagery/       Sentinel-2 search (Earth Search STAC), per-day mosaics, indices, rendering
  instruments/   water extent and burn severity, with versioned SOPs
  truth/         USBR reservoir records, NIFC fire perimeters
  studies/       month-by-month series, fire pre/post study, grading and charts
  agents/        tool-calling runtime, director, water lab, fire lab, reviewer, number audit
  institute.py   one research cycle: plan → measure → audit → review → rule → re-measure → bulletin
  web.py         mission-control API
web/             mission-control front end, Capy mascots, demo slides
scripts/         baselines, methods study, mascot generator, demo video
```

## Data and credits

- Contains modified Copernicus Sentinel data 2018–2026 (Sentinel-2 L2A via Element 84 Earth Search on AWS Open Data).
- U.S. Bureau of Reclamation, Lake Mead end-of-month elevations. NIFC WFIGS interagency fire perimeters.
- Capy the capybara is original SVG drawn in code (`scripts/make_mascot.py`).
- Demo narration: AssemblyAI text-to-speech.

## Limitations

- Lake areas are measured at 40 m; scenes that fail quality gates are rejected rather than guessed (73 of 106 months
  pass the default gates, 30 pass the strict gates of cycle-5's SOP v2).
- Fire maps compare one pre-fire and one post-fire scene; perimeters include unburned islands, so burned area is
  expected to be below the official area.
- SAM 3.1 as a third, vision-based water instrument is designed but not wired in (it needs the Meta Model API).
