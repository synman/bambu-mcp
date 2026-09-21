"""
tools/discovery.py — SSDP discovery tools for Bambu Lab printers.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


def discover_printers(timeout_seconds: int = 15) -> dict:
    """Discover Bambu Lab printers on the local network using SSDP.

    WHEN to use: to find printers on the LAN and get the name, IP address and serial number that
    add_printer needs, before any printer has been configured.

    Sibling disambiguation: ``discover_printers`` scans the network for printers that announce
    themselves, whether or not they are configured here. ``get_configured_printers`` lists only the
    printers already added to this server, and ``add_printer`` configures a discovered printer
    (this tool never adds one).

    Args:
        timeout_seconds: How long to listen for SSDP multicast announcements, in seconds
            (default 15). The scan stops at the timeout and returns the printers heard so far; it
            does not continue in the background. The call blocks for the full timeout.

    Returns:
        On success, ``{"discovered": [{name, ip, serial, model, model_decoded, bind_state,
        connect_state, firmware_version}], "count": int, "note": str}``, one entry per printer
        (de-duplicated by serial). ``name`` is the printer's announced factory device name (for
        example "3DP-094-913"), not a friendly name; choose your own name for ``add_printer``.
        ``model`` is the raw SSDP model code (for example "O1D"); ``model_decoded`` is the
        PrinterModel enum name inferred from the first three characters of the serial and reads
        "UNKNOWN" for an unrecognised prefix. ``bind_state`` is the raw DevBind header (for example
        "free") and ``connect_state`` is the raw DevConnect header, the connection mode (for
        example "lan"), not a reachability check; both are "" when the printer omitted the header.
        ``count`` is 0 and ``discovered`` is empty when no printer was heard within the timeout,
        but see Notes: 0 does not prove there are no printers. On failure, ``{"error": "Discovery
        failed: <reason>"}``.

    Notes:
        This tool only listens: it binds UDP port 2021 on this host for the duration of the scan
        and sends nothing to any printer. If that port is already bound (another
        ``discover_printers`` call in flight, Bambu Studio, or any other listener), the bind fails
        inside the discovery thread and no error reaches this tool: it still blocks for the full
        timeout and returns ``count`` 0 with no ``error`` key. Do not run two scans concurrently,
        and retry if an empty result is unexpected. access_code is NOT discoverable via SSDP and
        must be obtained from the printer's own LAN settings (Network > LAN > Access Code). After
        discovery, use ``add_printer(name, ip, serial, access_code, user_permission=True)`` once
        the operator has approved it; without ``user_permission`` the call changes nothing and
        returns a refusal string. The ``note`` string in the result still shows the four-argument
        form without ``user_permission``.
    """
    log.debug("discover_printers: called timeout=%d", timeout_seconds)
    log.info("discover_printers: starting BambuDiscovery timeout=%d", timeout_seconds)
    try:
        from bpm.bambudiscovery import BambuDiscovery

        discovery = BambuDiscovery(discovery_timeout=timeout_seconds)
        discovery.start()
        log.debug("discover_printers: BambuDiscovery started")

        log.debug("discover_printers: discovery running, waiting...")
        while discovery.running:
            time.sleep(0.5)

        log.info("discover_printers: discovery complete, found %d printers", len(discovery.discovered_printers))
        results = []
        for _usn, p in discovery.discovered_printers.items():
            results.append({
                "name": p.dev_name,
                "ip": p.location,
                "serial": p.usn,
                "model": p.dev_model,
                "model_decoded": (
                    p.decoded_model.name
                    if hasattr(p.decoded_model, "name")
                    else str(p.decoded_model)
                ),
                "bind_state": p.dev_bind,
                "connect_state": p.dev_connect,
                "firmware_version": p.dev_version,
            })

        log.info("discover_printers: found %d printers", len(results))
        log.debug("discover_printers: → %d results", len(results))
        return {
            "discovered": results,
            "count": len(results),
            "note": (
                "access_code is not discoverable via SSDP. "
                "Retrieve it from the printer's LAN settings (Network > Access Code). "
                "Then use add_printer(name, ip, serial, access_code) to configure."
            ),
        }
    except Exception as e:
        log.error("discover_printers: error: %s", e, exc_info=True)
        return {"error": f"Discovery failed: {e}"}
