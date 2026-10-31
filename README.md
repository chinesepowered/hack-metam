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
4. **Reviewer** verifies each claim, looks at the satellite evidence, re-measures key numbers from raw imagery,
   and applies the institute's publication standards (e.g. lake area must track the gauge at r ≥ 0.8; a burn map
   must overlap the perimeter at IoU ≥ 0.75).
5. **Director** rules: publish, or fix the procedure from evidence (a methods study, a versioned SOP that every lab
   follows) and send the study back.
6. A **bulletin** is published with the findings and an error ledger of what the institute caught in its own work.

## Results

Measured by the labs on real data. Under SOP v1 both studies failed review; the Director's SOP v2 fixed them.

| Study | SOP v1 | SOP v2 |
|---|---|---|
| Lake Mead surface area vs USBR gauge (73 months, 2018–2026) | Pearson r **0.27**, scatter ±49.6 km² | Pearson r **0.96**, scatter ±7.0 km² |
| Tartar Fire (Idaho, 2026) burn map vs official perimeter | IoU **0.66**, precision 0.69 | IoU **0.80**, precision 0.97 |

- **What went wrong in v1, water:** in low-sun winter scenes the atmospheric correction drives Lake Mead's
  reflectance to near zero in every band. The water index turns to noise and most of the lake is missed
  (5.6 km² measured on 2025-02-18). The labs' own evidence images show the lake rendered black. The methods study
  (`scripts/water_methods_eval.py`) showed a dark-water rule restores agreement with the gauge.
- **What went wrong in v1, fire:** crop harvest, seasonal drying and a neighbouring burn were mapped as burned,
  267 km² outside the perimeter. SOP v2 limits counting to the incident's surroundings and subtracts the seasonal
  background measured in an unburned ring around the fire (the dNBR offset method).
- **Context the Water Lab reported:** USBR's end-of-month record shows Lake Mead at 1,037.8 ft on 2026-09-30,
  its lowest since 1940, below the July 2022 low of 1,040.92 ft.

## Meta models

| Role | Model | How it is used |
|---|---|---|
| Director, labs, reviewer | **Muse Spark Open** (`meta-models/muse-spark-open-1026`, open weights, served by Baseten) | Tool calling, multi-step agent loops, vision over satellite evidence images, reasoning |
| Director / reviewer, optional | Muse Wonder / Muse Spark 1.3 (Meta Model API) | Opt in with `ORBITAL_META_ROLES=1` and `MODEL_API_KEY` (see `orbital.institute.roles()`) |

Structured results come back through tool calls rather than `response_format=json_schema`, which degenerates into
whitespace on the Baseten deployment. Evidence images are capped at 8 per request, the deployment's limit.

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

- Lake areas are measured at 40 m; months whose shoreline is cloudy are rejected rather than guessed (73 of 106
  months pass the quality gates).
- Fire maps compare one pre-fire and one post-fire scene; perimeters include unburned islands, so burned area is
  expected to be below the official area.
- SAM 3.1 as a third, vision-based water instrument is designed but not wired in (it needs the Meta Model API).
