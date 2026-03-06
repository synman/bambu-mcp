"""
tools/detectors.py — AI/X-Cam detector control tools for Bambu Lab printers.

All write tools require user_permission=True.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

from session_manager import session_manager


def _no_printer(name: str) -> str:
    return f"Error: Printer '{name}' not connected."


def _permission_denied() -> str:
    return "Error: user_permission must be True to perform this action."


def get_detector_settings(name: str) -> dict:
    """
    Return the current state of all X-Cam AI detector settings on the printer.

    Reads from the local BambuConfig which is kept in sync with printer telemetry.
    Includes enabled/disabled state and sensitivity for each supported detector.
    Detector names returned: 'buildplate_marker_detector' = verifies the correct build plate
    type is loaded before starting a print. 'purgechutepileup_detector' = detects if purged
    filament is piling up in the purge chute (can cause jams). 'nozzleclumping_detector' =
    detects filament clumping around the nozzle tip. 'spaghetti_detector' = detects loose
    spaghetti-like strands indicating a print failure. 'airprinting_detector' = detects the
    nozzle extruding into open air (clog). Each returns enabled (bool) and sensitivity
    ('low'/'medium'/'high').
    """
    log.debug("get_detector_settings: called for name=%s", name)
    config = session_manager.get_config(name)
    if config is None:
        log.warning("get_detector_settings: printer %s not connected", name)
        return {"error": f"Printer '{name}' not connected"}
    result = {
        "spaghetti_detector": {
            "enabled": config.spaghetti_detector,
            "sensitivity": config.spaghetti_detector_sensitivity,
            "supported": config.capabilities.has_spaghetti_detector_support,
        },
        "buildplate_marker_detector": {
            "enabled": config.buildplate_marker_detector,
            "supported": config.capabilities.has_buildplate_marker_detector_support,
        },
        "airprinting_detector": {
            "enabled": config.airprinting_detector,
            "sensitivity": config.airprinting_detector_sensitivity,
            "supported": config.capabilities.has_airprinting_detector_support,
        },
        "purgechutepileup_detector": {
            "enabled": config.purgechutepileup_detector,
            "sensitivity": config.purgechutepileup_detector_sensitivity,
            "supported": config.capabilities.has_purgechutepileup_detector_support,
        },
        "nozzleclumping_detector": {
            "enabled": config.nozzleclumping_detector,
            "sensitivity": config.nozzleclumping_detector_sensitivity,
            "supported": config.capabilities.has_nozzleclumping_detector_support,
        },
        "nozzle_blob_detect": {
            "enabled": config.nozzle_blob_detect,
            "supported": config.capabilities.has_nozzle_blob_detect_support,
        },
        "air_print_detect": {
            "enabled": config.air_print_detect,
            "supported": config.capabilities.has_air_print_detect_support,
        },
    }
    log.debug("get_detector_settings: returning %d detector settings for %s", len(result), name)
    return result


def set_spaghetti_detection(
    name: str,
    enabled: bool,
    sensitivity: str = "medium",
    user_permission: bool = False,
) -> str:
    """
    Enable or disable the spaghetti / failed-print detector (X-Cam AI vision).

    When triggered, the printer halts the print. sensitivity must be one of:
    'low', 'medium', 'high'. Requires user_permission=True.
    """
    log.debug("set_spaghetti_detection: called for name=%s enabled=%s sensitivity=%s user_permission=%s", name, enabled, sensitivity, user_permission)
    if not user_permission:
        log.debug("set_spaghetti_detection: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_spaghetti_detection: printer not connected: %s", name)
        return _no_printer(name)
    try:
        from bpm.bambutools import DetectorSensitivity
        try:
            sens = DetectorSensitivity(sensitivity.lower())
        except ValueError:
            return f"Error: Invalid sensitivity '{sensitivity}'. Choose from: low, medium, high"
        log.debug("set_spaghetti_detection: calling printer.set_spaghetti_detector for %s", name)
        printer.set_spaghetti_detector(enabled, sens)
        log.debug("set_spaghetti_detection: command sent to %s", name)
        return f"Spaghetti detector {'enabled' if enabled else 'disabled'} (sensitivity: {sensitivity}) on '{name}'."
    except Exception as e:
        log.error("set_spaghetti_detection: error for %s: %s", name, e, exc_info=True)
        return f"Error setting spaghetti detector on '{name}': {e}"


def set_buildplate_marker_detection(
    name: str,
    enabled: bool,
    user_permission: bool = False,
) -> str:
    """
    Enable or disable the buildplate ArUco marker detector (X-Cam AI vision).

    Build plates have printed ArUco markers (visual fiducial patterns) on their surface.
    The camera reads these markers before the print starts to verify the plate type
    (e.g. textured PEI vs. smooth PEI). If the plate is incompatible with the sliced print
    settings, the printer pauses. Disable this if your plate's markers are worn or obscured.
    When enabled, the camera verifies the build surface is compatible before
    starting a print. Requires user_permission=True.
    """
    log.debug("set_buildplate_marker_detection: called for name=%s enabled=%s user_permission=%s", name, enabled, user_permission)
    if not user_permission:
        log.debug("set_buildplate_marker_detection: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_buildplate_marker_detection: printer not connected: %s", name)
        return _no_printer(name)
    try:
        log.debug("set_buildplate_marker_detection: calling printer.set_buildplate_marker_detector for %s", name)
        printer.set_buildplate_marker_detector(enabled)
        log.debug("set_buildplate_marker_detection: command sent to %s", name)
        return f"Buildplate marker detector {'enabled' if enabled else 'disabled'} on '{name}'."
    except Exception as e:
        log.error("set_buildplate_marker_detection: error for %s: %s", name, e, exc_info=True)
        return f"Error setting buildplate marker detector on '{name}': {e}"


def set_first_layer_inspection(
    name: str,
    enabled: bool,
    user_permission: bool = False,
) -> str:
    """
    Enable or disable the first-layer inspection (LiDAR/camera scan after layer 1).

    After the first layer completes, a LiDAR (laser distance sensor, built into X1/H2D
    series) or camera scans the surface to verify the layer adhered correctly. If adhesion
    problems are detected (gaps, lifting corners, incomplete coverage), the printer pauses.
    Not available on printers without LiDAR (A1, P1 series) — the command is accepted but
    has no effect.
    The printer scans the first layer for adhesion issues and can pause or report
    problems. Requires an AI camera module (has_lidar capability).
    Requires user_permission=True.
    """
    log.debug("set_first_layer_inspection: called for name=%s enabled=%s user_permission=%s", name, enabled, user_permission)
    if not user_permission:
        log.debug("set_first_layer_inspection: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_first_layer_inspection: printer not connected: %s", name)
        return _no_printer(name)
    try:
        cmd = {
            "xcam": {
                "command": "xcam_control_set",
                "control": enabled,
                "enable": enabled,
                "module_name": "first_layer_inspector",
                "print_halt": False,
                "sequence_id": "0",
            }
        }
        log.debug("set_first_layer_inspection: sending xcam command for %s", name)
        printer.send_anything(json.dumps(cmd))
        log.debug("set_first_layer_inspection: command sent to %s", name)
        return f"First layer inspection {'enabled' if enabled else 'disabled'} on '{name}'."
    except Exception as e:
        log.error("set_first_layer_inspection: error for %s: %s", name, e, exc_info=True)
        return f"Error setting first layer inspection on '{name}': {e}"


def set_air_printing_detection(
    name: str,
    enabled: bool,
    user_permission: bool = False,
) -> str:
    """
    Enable or disable the air-printing / no-extrusion detector (X-Cam AI vision).

    When triggered, the printer halts because the nozzle is detected to be
    extruding into open air (indicating a clog or grinding condition).
    Requires user_permission=True.
    """
    log.debug("set_air_printing_detection: called for name=%s enabled=%s user_permission=%s", name, enabled, user_permission)
    if not user_permission:
        log.debug("set_air_printing_detection: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_air_printing_detection: printer not connected: %s", name)
        return _no_printer(name)
    try:
        from bpm.bambutools import DetectorSensitivity
        log.debug("set_air_printing_detection: calling printer.set_airprinting_detector for %s", name)
        printer.set_airprinting_detector(enabled, DetectorSensitivity.MEDIUM)
        log.debug("set_air_printing_detection: command sent to %s", name)
        return f"Air-printing detector {'enabled' if enabled else 'disabled'} on '{name}'."
    except Exception as e:
        log.error("set_air_printing_detection: error for %s: %s", name, e, exc_info=True)
        return f"Error setting air-printing detector on '{name}': {e}"
