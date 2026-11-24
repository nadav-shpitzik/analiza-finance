"""Chava's finance desk, moving here from analiza-metrics (Analiza-Monday backlog 0301 slices D1-D5; the desk move plan).

D1 (24.11) is the skeleton: the monday sessionToken (auth.py), the gate by board membership (gate.py), the desk's reading
of monday (reading.py), and the page that proves it on a private board (routes.py, frontend/).

D2's server side (24.11): the desk's build from that reading (view.py), its Excel files (export.py), and the routes that
answer them (routes.py: the desk, its refresh, its totals, its files), ported from analiza-metrics' app/finance.py,
app/finance_export.py and app/finance_api.py at da671b2. The server reads only: what the desk writes, the person writes
in the browser (ADR-0188). Its one write is to its own database: how the desk is used, as closed words and counts
(usage.py, store.py: POST /api/usage into `desk_events`, migrations/005).
"""
