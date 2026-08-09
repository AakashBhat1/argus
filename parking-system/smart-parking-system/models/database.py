"""
Smart Parking System — Database Layer
Thread-safe SQLite with context manager.
"""
import sqlite3
import threading
import datetime
import config


_local = threading.local()
_lock = threading.Lock()


def get_connection():
    """Get a thread-local database connection."""
    if not hasattr(_local, "connection") or _local.connection is None:
        _local.connection = sqlite3.connect(config.DATABASE_PATH, check_same_thread=False)
        _local.connection.row_factory = sqlite3.Row
        _local.connection.execute("PRAGMA journal_mode=WAL")
        _local.connection.execute("PRAGMA foreign_keys=ON")
    return _local.connection


def execute(query, params=(), commit=False):
    """Execute a query and return results."""
    with _lock:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(query, params)
        if commit:
            conn.commit()
        return cursor


def executemany(query, params_list, commit=True):
    """Execute a query with many parameter sets."""
    with _lock:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.executemany(query, params_list)
        if commit:
            conn.commit()
        return cursor


def fetchone(query, params=()):
    """Fetch a single row."""
    cursor = execute(query, params)
    return cursor.fetchone()


def fetchall(query, params=()):
    """Fetch all rows."""
    cursor = execute(query, params)
    return cursor.fetchall()


def init_db():
    """Create all tables and seed initial data."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS parking_spaces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            space_id TEXT UNIQUE NOT NULL,
            zone TEXT DEFAULT 'A',
            floor TEXT DEFAULT 'G',
            is_occupied INTEGER DEFAULT 0,
            plate_text TEXT DEFAULT NULL,
            entry_time TEXT DEFAULT NULL
        );

        CREATE TABLE IF NOT EXISTS detected_plates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plate_text TEXT NOT NULL,
            state TEXT,
            timestamp TEXT NOT NULL,
            is_parked INTEGER DEFAULT 0,
            exit_time TEXT DEFAULT NULL,
            duration_minutes INTEGER DEFAULT NULL,
            confidence REAL DEFAULT 0,
            amount_paid REAL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS vehicle_profiles (
            plate_text TEXT PRIMARY KEY,
            profile_type TEXT DEFAULT 'normal',
            owner_name TEXT,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL,
            end_time TEXT DEFAULT NULL,
            plates_detected INTEGER DEFAULT 0,
            spaces_used INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            description TEXT NOT NULL,
            plate_text TEXT DEFAULT NULL,
            space_id TEXT DEFAULT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            role TEXT NOT NULL,
            mode TEXT DEFAULT 'user',
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
    """)
    conn.commit()

    # Seed vehicle profiles if empty
    prof_count = cursor.execute("SELECT COUNT(*) FROM vehicle_profiles").fetchone()[0]
    if prof_count == 0:
        cursor.executescript("""
            INSERT INTO vehicle_profiles (plate_text, profile_type, owner_name, notes) VALUES
            ('VIP-111', 'vip', 'CEO Office', 'CEO Personal Vehicle. VIP Parking access.'),
            ('VIP-777', 'vip', 'Managing Director', 'Welcome Managing Director!'),
            ('ALERT-99', 'blacklist', 'Stolen Vehicle DB', 'BOLO: Suspected stolen vehicle. Flag security!'),
            ('MH12AB1234', 'normal', 'Aakash Bhat', 'Regular employee vehicle.');
        """)
        conn.commit()

    # Seed parking spaces if empty
    count = cursor.execute("SELECT COUNT(*) FROM parking_spaces").fetchone()[0]
    if count == 0:
        floors = ['G', '1', '2']
        spaces_per_floor = [
            # Floor, space_id list, zone mapping
            ('G', [f'G-{i:02d}' for i in range(1, 9)]),
            ('1', [f'F1-{i:02d}' for i in range(1, 9)]),
            ('2', [f'F2-{i:02d}' for i in range(1, 9)])
        ]
        
        for floor, spaces in spaces_per_floor:
            for idx, space_id in enumerate(spaces):
                # Zone allocation: 1-3 is A, 4-6 is B, 7-8 is C
                if idx < 3:
                    zone = 'A'
                elif idx < 6:
                    zone = 'B'
                else:
                    zone = 'C'
                
                cursor.execute(
                    "INSERT INTO parking_spaces (space_id, zone, floor) VALUES (?, ?, ?)",
                    (space_id, zone, floor)
                )
        conn.commit()
        log_activity("system", "Initialized 24 parking spaces across 3 floors (G, 1, 2) and zones (A, B, C)")

    # Start a new session
    start_session()


def start_session():
    """Record a new system session."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    execute(
        "INSERT INTO sessions (start_time) VALUES (?)",
        (now,), commit=True
    )
    log_activity("system", "System session started")


def get_current_session_id():
    """Get the most recent session ID."""
    row = fetchone("SELECT id FROM sessions ORDER BY id DESC LIMIT 1")
    return row["id"] if row else None


def _add_free_spaces(num_to_add):
    """Add num_to_add new FREE spaces.
    Reuses the floor/zone/id scheme from init_db seeding for consistency:
    cycles floors G/1/2, uses G-/F1-/F2- prefixes, zones A/B/C by slot position,
    continues numbering (e.g. G-09, F1-09...) so no collision with base 01-08.
    """
    if num_to_add <= 0:
        return 0
    added = 0
    for _ in range(num_to_add):
        current = fetchone("SELECT COUNT(*) as c FROM parking_spaces")["c"]
        floor = ['G', '1', '2'][current % 3]
        pfx = {'G': 'G', '1': 'F1', '2': 'F2'}[floor]
        slot_num = (current // 3) + 1
        space_id = f"{pfx}-{slot_num:02d}"
        slot_in_floor = (slot_num - 1) % 8
        if slot_in_floor < 3:
            zone = 'A'
        elif slot_in_floor < 6:
            zone = 'B'
        else:
            zone = 'C'
        # Insert only if id not present (defensive)
        exists = fetchone("SELECT 1 FROM parking_spaces WHERE space_id = ?", (space_id,))
        if not exists:
            execute(
                "INSERT INTO parking_spaces (space_id, zone, floor, is_occupied) VALUES (?, ?, ?, 0)",
                (space_id, zone, floor), commit=True
            )
            added += 1
    return added


def log_activity(event_type, description, plate_text=None, space_id=None):
    """Log an event to the activity log."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    execute(
        "INSERT INTO activity_log (timestamp, event_type, description, plate_text, space_id) VALUES (?, ?, ?, ?, ?)",
        (now, event_type, description, plate_text, space_id),
        commit=True
    )


def close():
    """Close the thread-local connection."""
    if hasattr(_local, "connection") and _local.connection:
        _local.connection.close()
        _local.connection = None
