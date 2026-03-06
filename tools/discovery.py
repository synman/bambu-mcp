"""
tools/discovery.py — SSDP discovery tools for Bambu Lab printers.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


def discover_printers(timeout_seconds: int = 15) -> dict:
    """
    Discover Bambu Lab printers on the local network using SSDP.

    Listens for SSDP multicast announcements from Bambu Lab printers for up to
    timeout_seconds. Returns a list of discovered printers with name, IP address,
    serial number, model, bind state, and connect state.
    Returns all printers that respond within timeout_seconds. The scan stops at the
    timeout and returns results so far — it does not continue in the background.
    'bind_state' indicates whether the printer is bound to a Bambu cloud account.
    'connect_state' indicates whether the printer is currently accepting connections.

    Note: access_code is NOT discoverable via SSDP and must be obtained from the
    printer's own LAN settings (Network > LAN > Access Code). After discovery,
    use add_printer(name, ip, serial, access_code) to configure the printer.
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
