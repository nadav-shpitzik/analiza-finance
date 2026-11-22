"""Outbound TLS trust for the monday client (ported from analiza-metrics `app/tls.py`, 22.11).

HTTPX's default is certifi alone, and since 2026-10-22 a fresh Replit build fails TLS to monday with it ("unable to get
local issuer certificate": analiza-metrics' first build after it blocked every monday read). The platform's own roots
plus certifi verify. Never bypass verification.
"""

import ssl

import certifi


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context
