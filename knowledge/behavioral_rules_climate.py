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
no external source required — same class of fact as the ideal gas law).

This model applies once the bed has reached operating temperature (~100°C+).
There is a lag phase while the bed itself heats up.

---

## Empirical Constants — H2D with Optimal Heating Config

Measured session: 2026-03-14
Conditions: bed target 110°C, chamber target 65°C, aux fan 100%, exhaust fan 0%,
            starting from ambient (~22°C), 486 data points over 9.3 min of active heating.

  τ = 1035 s  (17.25 min)  — thermal time constant
  k = 0.0580 /min  — Newton heating rate per °C delta to target

  Lag phase: ~1.8 min  (bed heating from cold before chamber begins rising)

Instantaneous heating rates at various chamber temperatures (bed@110°C, target=65°C):
  22°C → 2.50 °C/min
  30°C → 2.04 °C/min
  40°C → 1.45 °C/min
  42°C → 1.33 °C/min  (measured 2026-03-14)
  50°C → 0.87 °C/min
  55°C → 0.58 °C/min
  60°C → 0.29 °C/min

ETA table from cold start (22°C ambient, optimal config):
  To 50°C:  ~17 min total
  To 55°C:  ~25 min total  (~23 min after lag phase)
  To 60°C:  ~37 min total  (~35 min after lag phase)
  To 63°C:  ~53 min total  (~51 min after lag phase)
  To 65°C:  ~88 min total  (theoretical; active heater sustains final degrees)

Note: τ refines as more data accumulates. With only 3 min of data, τ may read
~36 min (over-estimate). With 9+ min of data, τ converges to ~17 min.
Always prefer estimates from later in the heating curve.

---

## Optimal Heating Configuration

To heat the chamber as fast as possible:
  1. set_chamber_temp(name, temp=target, user_permission=True)   — activates chamber heater
  2. set_bed_temp(name, temp=110, user_permission=True)          — bed is primary heat source
  3. set_fan_speed(name, fan="exhaust", speed_percent=0, ...)    — retain heat
  4. set_fan_speed(name, fan="aux", speed_percent=100, ...)      — circulate heat

Issue all four commands in parallel (one agent turn). The bed at 110°C is the
dominant driver; the active chamber heater assists the final approach to target.

---

## Real-Time ETA Projection Algorithm

To project remaining time given live telemetry:

  1. Fetch monitoring series: get_monitoring_series(name, "chamber") and "bed"
  2. Find heat-start index: first point where bed > 25°C
  3. Build ct (chamber times, offset to 0) and cv (chamber values)
  4. Use only points where ct > lag_phase_s (108 s) to exclude lag noise
  5. Fit exponential: T(t) = T_target - (T_target - T0) * exp(-t/τ)
     using scipy.optimize.curve_fit with p0=[1035]
  6. Time to reach T_thresh from heat start:
       t_eta = τ * ln((T_target - T0) / (T_target - T_thresh))
  7. Remaining = max(0, t_eta - t_elapsed)

Reliability thresholds:
  < 3 min data:  τ estimate unreliable (error ±20 min); report as rough estimate
  3–6 min data:  τ estimate fair (error ±8 min)
  > 9 min data:  τ estimate converged (error ±2 min) — use with confidence

When data is sparse (< 3 min), report ETA with explicit caveat:
  "~X min (estimate based on early data — will sharpen as chamber heats)"

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
