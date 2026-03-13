#!/usr/bin/env python3
"""
H2D Idle Nozzle Heat Timeout Calibration.

Measures how long the H2D firmware waits before silently resetting an elevated nozzle
target to 38°C when no print is active (gcode_state: IDLE, FINISH, or FAILED).

This timeout is critical for camera calibration scripts that heat nozzles while IDLE:
  - corner_calibration.py (detect_nozzle_heat_toggle) — up to 135s exposure
  - nozzle_compare.py — up to 120s exposure
Both can exceed the ~170s estimated timeout; the dual-layer keepalive in heat_and_wait()
must use the calibrated value.

Protocol:
  1. Confirm printer is IDLE (gcode_state != RUNNING/PREPARE/PAUSE)
  2. Set T0 to TARGET_TEMP via PATCH /api/set_tool_target_temp (Tier 1 — never M104)
  3. Poll GET /api/printer every POLL_INTERVAL_S — record target temperature
  4. On first drop (target != TARGET_TEMP): record elapsed time (= timeout)
  5. Immediately re-assert TARGET_TEMP via set_nozzle_temp — record t_restore
  6. Continue polling: if second drop occurs, compare interval to original timeout
     (short = restoring target resets the firmware countdown; same = it does not)
  7. Output: IDLE_NOZZLE_HEAT_TIMEOUT_S, target_restore_resets_timer, tested_gcode_state

Requirements:
  - Printer IDLE and nozzle cool (< 50°C) before running
  - bambu-mcp server running on localhost
  - User must authorize nozzle heating (interactive prompt)

Usage:
  python3 calibrate_idle_nozzle_timeout.py [--target <°C>] [--trials <n>]

Defaults:
  --target 150     # well above T_IDLE=38°C but conservative; avoids plastic drool
  --trials 2       # two drops — enough to test whether restore resets the countdown
"""

import argparse
import json
import time
import sys
import urllib.error
import urllib.request


# ── Constants ─────────────────────────────────────────────────────────────────

PRINTER_NAME   = "H2D"
TARGET_TEMP    = 150        # °C — elevated target to heat; low enough to avoid drool
POLL_INTERVAL  = 0.5        # s — default poll interval; ±0.5s accuracy
MAX_WAIT       = 3600.0     # s — safety ceiling; if no reset seen in 1 hour, abort
T_IDLE         = 38         # °C — expected reset target (matches corner_calibration.py)
MAX_TRIALS     = 3          # maximum number of drops to observe


# ── API discovery ──────────────────────────────────────────────────────────────

def _find_api_base() -> str:
    """Discover the bambu-mcp HTTP API port by scanning the ephemeral pool."""
    for port in range(49152, 49252):
        try:
            url = f"http://localhost:{port}/api/server_info"
            with urllib.request.urlopen(url, timeout=0.5) as r:
                info = json.loads(r.read())
                if "api_port" in info:
                    return f"http://localhost:{info['api_port']}/api"
        except Exception:
            continue
    return "http://localhost:49152/api"


API_BASE = _find_api_base()


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _get(path: str) -> dict:
    url = f"{API_BASE}/{path}?printer={PRINTER_NAME}"
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def _patch(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{API_BASE}/{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def set_nozzle_temp(temp: int, extruder: int = 0) -> dict:
    """Set nozzle temperature via PATCH /api/set_tool_target_temp (Tier 1).

    NEVER use send_gcode/M104 for temperature — a dedicated HTTP route exists.
    Using M104 via send_gcode is a Tier 1 escalation violation.
    """
    return _patch("set_tool_target_temp", {
        "printer": PRINTER_NAME,
        "temp": temp,
        "extruder": extruder,
    })


def get_nozzle_target(extruder: int = 0) -> float:
    """Read the current nozzle temperature target via GET /api/printer.

    Primary path: _printer_state.extruders[extruder].temp_target
    Fallback: _printer_state.active_nozzle_temp_target (extruder 0 only)
    """
    state = _get("printer")
    ps = state.get("_printer_state", {})
    extruders = ps.get("extruders", [])
    if isinstance(extruders, list) and len(extruders) > extruder:
        return float(extruders[extruder].get("temp_target", -1))
    # Fallback for extruder 0
    return float(ps.get("active_nozzle_temp_target", -1))


def get_gcode_state() -> str:
    """Read the current gcode_state string."""
    try:
        state = _get("printer")
        return state.get("_printer_state", {}).get("gcode_state", "UNKNOWN")
    except Exception:
        return "UNKNOWN"


# ── Pre-flight ─────────────────────────────────────────────────────────────────

def preflight_check() -> str:
    """Confirm printer is idle and nozzle is cool. Returns gcode_state."""
    print("Pre-flight: checking printer state...")
    try:
        gcode_state = get_gcode_state()
    except Exception as e:
        print(f"  ERROR: cannot reach bambu-mcp server: {e}")
        sys.exit(1)

    if gcode_state in ("RUNNING", "PREPARE"):
        print(f"  ERROR: printer is {gcode_state} — calibration requires IDLE/FINISH/FAILED")
        sys.exit(1)

    print(f"  gcode_state: {gcode_state} ✓")

    # Check nozzle temp — must not already be hot
    try:
        target = get_nozzle_target(extruder=0)
        print(f"  T0 target: {target}°C")
        if target > 60:
            print(f"  WARNING: T0 is already targeting {target}°C — calibration may be unreliable")
    except Exception:
        pass

    return gcode_state


# ── Calibration loop ───────────────────────────────────────────────────────────

CONFIRM_TIMEOUT = 15.0  # s — max time to wait for telemetry to reflect a newly set target


def run_calibration(target_temp: int, num_trials: int, poll_interval: float = POLL_INTERVAL) -> dict:
    """Run the idle nozzle timeout calibration.

    Two phases per trial:
      Confirm phase: after set_nozzle_temp(), poll until telemetry shows target == target_temp.
                     This guards against reading stale telemetry before the printer has
                     acknowledged the newly set target — which would falsely appear as a reset.
      Watch phase:   once confirmed, start the timer — poll for target != target_temp.
                     The elapsed time from confirmation to drift is the true firmware timeout.

    Returns a result dict with:
      - IDLE_NOZZLE_HEAT_TIMEOUT_S: measured firmware reset timeout
      - target_restore_resets_timer: whether restoring the target resets the countdown
      - trials: list of per-trial observations
      - tested_gcode_state: gcode_state during the run
    """
    gcode_state = preflight_check()

    print(f"\nCalibration parameters:")
    print(f"  Printer:       {PRINTER_NAME}")
    print(f"  Target temp:   {target_temp}°C")
    print(f"  Trials:        {num_trials}")
    print(f"  Poll interval: {poll_interval}s")
    print(f"  Max wait:      {MAX_WAIT}s")
    print(f"  gcode_state:   {gcode_state}")
    print()

    # User authorization gate
    print(f"This calibration will heat T0 to {target_temp}°C and hold it while IDLE.")
    confirm = input("Authorize nozzle heating? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Aborted.")
        sys.exit(0)

    trials = []
    timeout_samples = []

    for trial in range(num_trials):
        print(f"\n─── Trial {trial + 1} of {num_trials} ───")

        # Set the target
        print(f"  Setting T0 → {target_temp}°C via PATCH /api/set_tool_target_temp ...")
        set_nozzle_temp(target_temp, extruder=0)
        t_sent = time.time()

        # ── Confirm phase: wait until telemetry reflects the new target ──────────
        # Without this, a poll read before MQTT propagates gives a stale value that
        # looks like a firmware reset — producing a falsely short timeout measurement.
        print(f"  Waiting for telemetry confirmation (max {CONFIRM_TIMEOUT}s)...")
        t_confirmed = None
        while time.time() - t_sent < CONFIRM_TIMEOUT:
            time.sleep(poll_interval)
            try:
                current_target = get_nozzle_target(extruder=0)
            except Exception as e:
                print(f"  confirm poll error: {e}")
                continue
            elapsed_since_send = time.time() - t_sent
            print(f"  [{elapsed_since_send:5.2f}s] T0 target: {current_target}°C (waiting for {target_temp}°C)")
            if current_target == target_temp:
                t_confirmed = time.time()
                print(f"  Target confirmed in telemetry after {elapsed_since_send:.2f}s ✓")
                break

        if t_confirmed is None:
            print(f"  ERROR: telemetry never confirmed {target_temp}°C within {CONFIRM_TIMEOUT}s")
            print(f"  Possible causes: API error, firmware rejected the target, or propagation >15s")
            trials.append({"trial": trial + 1, "timeout_s": None, "error": "confirm_timeout"})
            continue

        # ── Watch phase: time from confirmation until firmware resets the target ──
        print(f"  Confirmed. Watching for firmware reset (poll every {poll_interval}s)...")
        reset_elapsed = None
        reset_target  = None

        while time.time() - t_confirmed < MAX_WAIT:
            time.sleep(poll_interval)
            elapsed = time.time() - t_confirmed

            try:
                current_target = get_nozzle_target(extruder=0)
            except Exception as e:
                print(f"  [{elapsed:6.2f}s] poll error: {e}")
                continue

            print(f"  [{elapsed:6.2f}s] T0 target: {current_target}°C", end="")

            if current_target != target_temp:
                reset_elapsed = elapsed
                reset_target  = current_target
                print(f"  ← RESET detected (was {target_temp}, now {current_target})")
                break
            else:
                print()

        if reset_elapsed is None:
            print(f"  No firmware reset observed within {MAX_WAIT}s — aborting trial")
            trials.append({"trial": trial + 1, "timeout_s": None, "error": "no_reset"})
            continue

        timeout_samples.append(reset_elapsed)
        print(f"  Timeout measured: {reset_elapsed:.2f}s")

        # Restore the target and note whether it re-confirms quickly (resets the countdown)
        print(f"  Restoring target to {target_temp}°C ...")
        set_nozzle_temp(target_temp, extruder=0)
        t_restore_sent = time.time()

        trials.append({
            "trial": trial + 1,
            "timeout_s": reset_elapsed,
            "reset_to": reset_target,
            "t_restore_sent": t_restore_sent - t_confirmed,
        })

    # Turn off nozzle heating at end
    print(f"\nCalibration complete. Turning off T0 heating...")
    set_nozzle_temp(0, extruder=0)

    # Compute result
    if not timeout_samples:
        print("ERROR: no usable timeout samples — calibration failed")
        return {"error": "no_samples", "trials": trials, "tested_gcode_state": gcode_state}

    avg_timeout = sum(timeout_samples) / len(timeout_samples)

    # Detect whether restoring the target resets the firmware countdown
    # If trial 2 timeout is similar to trial 1, the restore DID reset the countdown
    # If trial 2 timeout is much shorter, the countdown was not reset
    target_restore_resets_timer = None
    if len(timeout_samples) >= 2:
        ratio = timeout_samples[1] / timeout_samples[0]
        target_restore_resets_timer = ratio > 0.7  # >70% of original → countdown reset
        print(f"\nTimer-reset check: trial 1={timeout_samples[0]:.1f}s, trial 2={timeout_samples[1]:.1f}s")
        print(f"  ratio={ratio:.2f} → restore {'RESETS' if target_restore_resets_timer else 'does NOT reset'} the firmware countdown")

    result = {
        "IDLE_NOZZLE_HEAT_TIMEOUT_S": round(avg_timeout, 1),
        "IDLE_HEAT_KEEPALIVE_S": round(avg_timeout * 0.75, 1),
        "target_restore_resets_timer": target_restore_resets_timer,
        "trials": trials,
        "tested_gcode_state": gcode_state,
        "samples": timeout_samples,
    }

    return result


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibrate H2D idle nozzle heat timeout — measures when firmware resets "
                    "an elevated nozzle target to 38°C during IDLE/FINISH/FAILED states."
    )
    parser.add_argument("--target",  type=int, default=TARGET_TEMP,
                        help=f"Target nozzle temperature °C (default {TARGET_TEMP})")
    parser.add_argument("--trials",  type=int, default=2,
                        help=f"Number of timeout drops to observe (default 2, max {MAX_TRIALS})")
    parser.add_argument("--poll-interval", type=float, default=POLL_INTERVAL,
                        help=f"Poll interval in seconds for both confirm and watch phases (default {POLL_INTERVAL})")
    args = parser.parse_args()

    if args.trials > MAX_TRIALS:
        print(f"ERROR: max trials is {MAX_TRIALS}")
        sys.exit(1)

    result = run_calibration(args.target, args.trials, poll_interval=args.poll_interval)

    print("\n" + "═" * 60)
    print("CALIBRATION RESULT")
    print("═" * 60)
    if "error" in result:
        print(f"  ERROR: {result['error']}")
    else:
        print(f"  IDLE_NOZZLE_HEAT_TIMEOUT_S = {result['IDLE_NOZZLE_HEAT_TIMEOUT_S']}")
        print(f"  IDLE_HEAT_KEEPALIVE_S      = {result['IDLE_HEAT_KEEPALIVE_S']}  (= timeout × 0.75)")
        print(f"  target_restore_resets_timer = {result['target_restore_resets_timer']}")
        print(f"  tested_gcode_state          = {result['tested_gcode_state']}")
        print()
        print("  ──── Bake into corner_calibration.py: ────")
        print(f"  IDLE_NOZZLE_HEAT_TIMEOUT_S  = {result['IDLE_NOZZLE_HEAT_TIMEOUT_S']}  # [VERIFIED: empirical YYYY-MM-DD]")
        print(f"  IDLE_HEAT_KEEPALIVE_S       = IDLE_NOZZLE_HEAT_TIMEOUT_S * 0.75   # = {result['IDLE_HEAT_KEEPALIVE_S']}s")
        print(f"  IDLE_HEAT_POLL_INTERVAL_S   = 10                                   # reactive check interval")
        if result.get("target_restore_resets_timer") is False:
            print()
            print("  WARNING: restoring the target does NOT reset the firmware countdown.")
            print("  The proactive keepalive timer in heat_and_wait() is essential —")
            print("  reactive-only polling will not prevent the reset from firing again.")

    print("═" * 60)

    # Write JSON result for reference
    out_path = "/tmp/idle_nozzle_timeout_calibration.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nResult saved to {out_path}")


if __name__ == "__main__":
    main()
