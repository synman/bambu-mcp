"""
behavioral_rules_climate.py — Chamber heating model, ETA projection, empirical constants.

Sub-topic of behavioral_rules. Access via get_knowledge_topic('behavioral_rules/climate').
"""

from __future__ import annotations

BEHAVIORAL_RULES_CLIMATE_TEXT: str = """
# Behavioral Rules — Climate / Chamber Heating

---

## Chamber Heat-Up Model (H2D)

The H2D has an active chamber heater in addition to the heated bed. The chamber
temperature follows Newton's law of heating — an exponential approach to the set-point.
[VERIFIED: BambuStudio] Bambu ABS @BBL H2D.json specifies "chamber_temperatures": ["65"],
confirming active chamber control is used. Confirmed 2026-03-14.

    T(t) = T_target - (T_target - T_start) * exp(-t / τ)

where:
  T_target = chamber temperature target (°C) — set via set_chamber_temp()
  T_start  = chamber temperature at start of heating (°C)
  t        = elapsed time since active heating began (seconds)
  τ        = thermal time constant (seconds) — see empirical values below

Newton's law of heating formula: standard thermodynamics (universally accepted,
no external source required — same class as the ideal gas law).

This model applies once the bed has reached operating temperature (~100°C+).
There is a lag phase while the bed itself heats up.

---

## Empirical Constants — H2D with Optimal Heating Config

[VERIFIED: empirical 2026-03-14, ~1763 data points total (602 early-phase + 1161 full-curve),
 cold start T_start=22°C, bed=110°C, chamber target=65°C, aux fan=100%, exhaust=0%]

Session span: 12:16 (22°C) → 12:44:23 (65°C) — 28.37 min total.

PRIMARY CONSTANTS (full-curve fit, 22°C cold-start baseline — use these):
  τ = 560 s  (9.3 min)  — thermal time constant, median of 5 milestone fits
  Lag phase: ~1.3 min at ambient before chamber temperature begins rising

OBSOLETE ESTIMATES (provenance documented — do not use):
  τ = 820 s — first 9.3 min only (early-phase over-estimate; fast bed-driven initial
               ramp skews τ high when measured from lag-end to first curve inflection)
  τ = 1035 s — prior-session first estimate (insufficient data; discard)

All four configuration variables matter. If ANY deviates from optimal, τ degrades
toward 820 s or worse. The four variables are:
  - Bed at 110°C         (dominant secondary heat source via radiation/convection)
  - Exhaust fan at 0%    (closed — retains heat, prevents venting)
  - Aux fan at 100%      (circulates air, equalizes distribution — does NOT exhaust heat)
  - Chamber target 65°C  (active heater engaged at this target)

Model heating rates (Newton's law dT/dt = (T_target − T_cur)/τ, τ=560s):
  22°C → 4.61 °C/min  (actual first 5 min average: 4.0 °C/min)
  30°C → 3.75 °C/min
  40°C → 2.68 °C/min
  42°C → 2.46 °C/min
  50°C → 1.61 °C/min
  55°C → 1.07 °C/min
  60°C → 0.54 °C/min  (actual 60→63°C average: 0.60 °C/min — near-target divergence mild)

MODEL ACCURACY NOTE: τ=560s is the best single-parameter fit for the full curve but
diverges in two regimes:
  - Early phase (22°C→42°C): bed at 110°C drives a faster initial ramp than the
    Newton's law exponential predicts; model is ~2 min slow at the 5-min mark.
  - Late phase (>62°C): active PID drives the final degrees faster than the passive
    exponential asymptote; model over-estimates remaining time by up to 5 min at 63°C.
  Use the empirical milestone table below for most-accurate projections.

---

## Empirical Milestone Table — Cold Start (22°C) to 65°C

ACTUAL measured milestones from 2026-03-14 session (optimal config):
  T=22°C  →  t=  0.0 min  (cold start)
  T=42°C  →  t=  5.0 min
  T=49°C  →  t= 10.0 min
  T=55°C  →  t= 15.0 min
  T=60°C  →  t= 20.0 min
  T=63°C  →  t= 25.0 min
  T=65°C  →  t= 28.4 min  (65°C reached, heating complete)

This table is the canonical reference for cold-start ETA projections from 22°C ambient.
Always prefer this table over model-derived ETAs when starting from near-ambient.

---

## Remaining Time Lookup (current T → 65°C, cold start from 22°C ambient)

Empirical column: most accurate — derived from milestone interpolation (28.4 min total).
Model column: conservative upper bound using τ=560s Newton's law formula.

  Current °C | Elapsed est | Empirical remaining | Model remaining
  -----------+-------------+---------------------+----------------
    25°C     |   ~0.8 min  |      ~27.6 min      |    ~37 min
    32°C     |   ~2.1 min  |      ~26.3 min      |    ~36 min
    40°C     |   ~4.5 min  |      ~23.9 min      |    ~33 min
    42°C     |   ~5.0 min  |      ~23.4 min      |    ~32 min
    45°C     |   ~7.1 min  |      ~21.3 min      |    ~31 min
    50°C     |  ~10.8 min  |      ~17.6 min      |    ~28 min
    55°C     |  ~15.0 min  |      ~13.4 min      |    ~25 min
    58°C     |  ~18.0 min  |      ~10.4 min      |    ~21 min
    60°C     |  ~20.0 min  |       ~8.4 min      |    ~18 min
    62°C     |  ~23.3 min  |       ~5.1 min      |    ~13 min
    63°C     |  ~25.0 min  |       ~3.4 min      |    ~10 min
    64°C     |  ~26.7 min  |       ~1.7 min      |     ~3 min  (PID range)
    65°C     |   complete  |           done      |       done

Remaining time formula for intermediate temperatures (Newton's law):
    remaining = τ × ln((T_target − T_current) / (T_target − 64)) + 3 min PID
    where τ=560s and the +3 min accounts for PID completion of the last degree.
    Use this formula when T_current falls between milestone values.

Above 62°C: active PID drives the final approach faster than the model predicts;
empirical column is more accurate; model column over-estimates by 5–7 min in this zone.

---

## Optimal Heating Configuration

To heat the chamber as fast as possible:
  1. set_chamber_temp(name, temp=target, user_permission=True)   — activates chamber heater
  2. set_bed_temp(name, temp=110, user_permission=True)          — bed is primary heat source
  3. set_fan_speed(name, fan="exhaust", speed_percent=0, ...)    — retain heat
  4. set_fan_speed(name, fan="aux", speed_percent=100, ...)      — circulate heat

Issue all four commands in parallel (one agent turn). The bed at 110°C is the
dominant driver; the active chamber heater assists the final approach to target.

Aux fan at 100% circulates air for uniform temperature distribution. It does NOT
exhaust heat outside the chamber. Exhaust at 0% (fully closed) is essential — any
exhaust airflow vents heat and significantly degrades performance (τ rises toward 820s+).

---

## Real-Time ETA Projection Algorithm

To project remaining time given live telemetry:

  1. Fetch monitoring series: get_monitoring_series(name, "chamber") and "bed"
  2. Find heat-start index: first point where bed > 25°C
  3. Build ct (chamber times, offset to 0) and cv (chamber values)
  4. Use only points where ct > lag_phase_s (78 s = 1.3 min) to exclude lag noise
  5. Cross-check T_current against milestone table — if within ±3°C of a milestone,
     interpolate remaining directly from milestones (most accurate path)
  6. If milestone cross-check unavailable, fit exponential:
       T(t) = T_target - (T_target - T0) * exp(-t/τ)
       using scipy.optimize.curve_fit with p0=[560]
  7. Cross-check fitted τ: if τ > 700s or τ < 400s, report that conditions deviate
     from optimal (exhaust may be open, bed may be low, etc.)

Reliability thresholds:
  < 3 min data:  use milestone table as primary ETA reference
  3–6 min data:  τ estimate fair (error ±5 min); cross-check against milestones
  > 9 min data:  τ estimate converged (error ±2 min) — use with confidence

When data is sparse (< 3 min), report ETA from milestone table with explicit caveat:
  "~X min remaining (milestone table reference; will sharpen as heating progresses)"

---

## Practical Thresholds for ABS Printing

  Official H2D target (ABS):     65°C  [VERIFIED: BambuStudio] Bambu ABS @BBL H2D.json
                                        "chamber_temperatures": ["65"]. Confirmed 2026-03-14.
  Minimum chamber temp for ABS:  ~40°C  [PROVISIONAL] engineering estimate; not in Bambu source
  Recommended for ABS:           50–55°C [PROVISIONAL] engineering estimate; not in Bambu source

When a user asks "is it ready to print ABS?", consider current chamber temp against
these thresholds, not just whether it has reached the set-point.

---

## Cross-Model Notes

[VERIFIED: BambuStudio, ha-bambulab — 2026-03-14]

Active chamber heating (H2-series only):
  H2D, H2DPRO, H2C, H2S — BambuStudio sets chamber_temperatures target for these
  printers; firmware actively controls a chamber heater element.
  Evidence: Bambu ABS @BBL H2D.json: "chamber_temperatures": ["65"]
            Bambu ABS @BBL H2C.json: "chamber_temperatures": ["65"]
            ha-bambulab models.py: Features.CHAMBER_TEMPERATURE ∈ h2_printers

Chamber sensor only (no active heater):
  X1, X1C, X1E, P2S — ha-bambulab reports CHAMBER_TEMPERATURE feature = True
  (sensor exists, firmware accepts target), BUT BambuStudio ABS profile has NO
  chamber_temperatures field → slicer does not set a target → passive heating only.
  Evidence: Bambu ABS @BBL X1C.json: no chamber_temperatures field

No chamber management:
  P1S, P1P — no CHAMBER_TEMPERATURE feature in ha-bambulab; no profile field
  A1, A1Mini — open-frame design; no enclosure, no chamber

For printers without active chamber heating:
  - The chamber will reach a lower equilibrium (~40–50°C max with bed@100°C)
  - τ is longer (~25–40 min depending on enclosure insulation)
  - set_chamber_temp() stores the target for external management only;
    no active heater is engaged
"""
