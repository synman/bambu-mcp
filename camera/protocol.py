"""
protocol.py — Model → camera protocol routing for Bambu Lab printers.

Two distinct protocols exist, selected by printer model:
  RTSPS    — X1C, X1, X1E, P2S, H2D, H2S   (port 322, standard RTSP over TLS)
  TCP+TLS  — A1, A1_MINI, P1P, P1S           (port 6000, Bambu proprietary binary)
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

from bpm.bambutools import PrinterModel

RTSPS_MODELS: frozenset[PrinterModel] = frozenset({
    PrinterModel.X1C,
    PrinterModel.X1,
    PrinterModel.X1E,
    PrinterModel.P2S,
    PrinterModel.H2D,
    PrinterModel.H2S,
})

TCP_TLS_MODELS: frozenset[PrinterModel] = frozenset({
    PrinterModel.A1,
    PrinterModel.A1_MINI,
    PrinterModel.P1P,
    PrinterModel.P1S,
})


def get_protocol(printer) -> str:
    """Return "rtsps", "tcp_tls", or "none" based on the printer model."""
    log.debug("get_protocol: called, model=%s", getattr(getattr(printer, 'config', None), 'printer_model', None))
    model = getattr(getattr(printer, "config", None), "printer_model", None)
    if model in RTSPS_MODELS:
        log.debug("get_protocol: selected rtsps protocol")
        return "rtsps"
    if model in TCP_TLS_MODELS:
        log.debug("get_protocol: selected tcp_tls protocol")
        return "tcp_tls"
    log.debug("get_protocol: no camera protocol for model=%s", model)
    return "none"


def get_rtsps_url(printer) -> str:
    """Return the RTSPS stream URL for X1/H2D series printers."""
    log.debug("get_rtsps_url: called for ip=%s", printer.config.hostname)
    ip = printer.config.hostname
    access_code = printer.config.access_code
    log.debug("get_rtsps_url: returning rtsps://bblp:<redacted>@%s:322/streaming/live/1", ip)
    return f"rtsps://bblp:{access_code}@{ip}:322/streaming/live/1"


def has_camera(printer) -> bool:
    """Return True if this printer model has a camera."""
    log.debug("has_camera: called")
    result = get_protocol(printer) != "none"
    log.debug("has_camera: returning %s", result)
    return result
