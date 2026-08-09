"""
Smart Parking System — Main Page Routes (RETIRED)

Legacy Jinja2 UI routes removed in Round 17 as part of final cutover.
The Flask app is now a pure headless JSON + SSE API.
main_bp is no longer imported or registered in app.py.
"""
# Blueprint left as None for safety (module is no longer loaded by create_app).
main_bp = None  # retired legacy UI routes (/, /detect, /parking, /admin)