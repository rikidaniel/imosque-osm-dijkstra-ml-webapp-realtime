from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_DIR / "data"
DB_DIR = DATA_DIR / "local_db"
DB_PATH = DB_DIR / "imosque.sqlite"
_INIT_LOCK = threading.Lock()
_INITIALIZED = False


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    with _INIT_LOCK:
        if _INITIALIZED:
            return
        _init_db_unlocked()
        _INITIALIZED = True


def _init_db_unlocked() -> None:
    with connect() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS datasets (
                dataset_id TEXT PRIMARY KEY,
                filename TEXT,
                raw_csv_path TEXT,
                enriched_json_path TEXT,
                processed INTEGER NOT NULL DEFAULT 0,
                profile_json TEXT,
                mosque_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mosques (
                dataset_id TEXT NOT NULL,
                mosque_id TEXT NOT NULL,
                name TEXT,
                address TEXT,
                province TEXT,
                kabko TEXT,
                kecamatan TEXT,
                kelurahan TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                rating REAL,
                review_count INTEGER,
                mosque_type TEXT,
                facilities_json TEXT,
                capacity_proxy TEXT,
                priority_score REAL,
                tier TEXT,
                data_quality_json TEXT,
                raw_json TEXT NOT NULL,
                PRIMARY KEY (dataset_id, mosque_id),
                FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_mosques_dataset_lat_lon
                ON mosques(dataset_id, latitude, longitude);
            CREATE INDEX IF NOT EXISTS idx_mosques_dataset_kabko
                ON mosques(dataset_id, kabko);
            CREATE INDEX IF NOT EXISTS idx_mosques_dataset_priority
                ON mosques(dataset_id, priority_score DESC);
            CREATE INDEX IF NOT EXISTS idx_mosques_dataset_tier
                ON mosques(dataset_id, tier);

            CREATE TABLE IF NOT EXISTS osm_graph_cache (
                cache_id TEXT PRIMARY KEY,
                graphml_path TEXT NOT NULL,
                south REAL,
                north REAL,
                west REAL,
                east REAL,
                buffer_km REAL,
                network_type TEXT,
                nodes INTEGER,
                edges INTEGER,
                size_mb REAL,
                updated_at TEXT NOT NULL
            );
            """
        )


def set_setting(key: str, value: str) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO app_settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, _now()),
        )


def get_setting(key: str) -> str | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else None


def upsert_dataset(
    dataset_id: str,
    *,
    filename: str | None = None,
    raw_csv_path: str | None = None,
    enriched_json_path: str | None = None,
    processed: bool = False,
    profile: Dict[str, Any] | None = None,
    mosque_count: int | None = None,
) -> None:
    init_db()
    profile_json = json.dumps(profile, ensure_ascii=False) if profile is not None else None
    now = _now()
    with connect() as conn:
        current = conn.execute(
            "SELECT created_at, mosque_count FROM datasets WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()
        created_at = current["created_at"] if current else now
        existing_count = int(current["mosque_count"]) if current else 0
        conn.execute(
            """
            INSERT INTO datasets(
                dataset_id, filename, raw_csv_path, enriched_json_path, processed,
                profile_json, mosque_count, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dataset_id) DO UPDATE SET
                filename = COALESCE(excluded.filename, datasets.filename),
                raw_csv_path = COALESCE(excluded.raw_csv_path, datasets.raw_csv_path),
                enriched_json_path = COALESCE(excluded.enriched_json_path, datasets.enriched_json_path),
                processed = excluded.processed,
                profile_json = COALESCE(excluded.profile_json, datasets.profile_json),
                mosque_count = excluded.mosque_count,
                updated_at = excluded.updated_at
            """,
            (
                dataset_id,
                filename,
                raw_csv_path,
                enriched_json_path,
                1 if processed else 0,
                profile_json,
                existing_count if mosque_count is None else int(mosque_count),
                created_at,
                now,
            ),
        )


def save_mosques(
    dataset_id: str,
    records: Sequence[Dict[str, Any]],
    *,
    profile: Dict[str, Any],
    raw_csv_path: str,
    enriched_json_path: str,
) -> None:
    init_db()
    upsert_dataset(
        dataset_id,
        filename=Path(raw_csv_path).name,
        raw_csv_path=raw_csv_path,
        enriched_json_path=enriched_json_path,
        processed=True,
        profile=profile,
        mosque_count=len(records),
    )
    rows = []
    for item in records:
        rows.append(
            (
                dataset_id,
                str(item.get("id", "")),
                item.get("name", ""),
                item.get("address", ""),
                item.get("province", ""),
                item.get("kabko", ""),
                item.get("kecamatan", ""),
                item.get("kelurahan", ""),
                float(item.get("latitude")),
                float(item.get("longitude")),
                float(item["rating"]) if item.get("rating") is not None else None,
                int(item["review_count"]) if item.get("review_count") is not None else None,
                item.get("mosque_type", ""),
                json.dumps(item.get("facilities", []), ensure_ascii=False),
                item.get("capacity_proxy", ""),
                float(item["priority_score"]) if item.get("priority_score") is not None else None,
                item.get("tier", ""),
                json.dumps(item.get("data_quality", {}), ensure_ascii=False),
                json.dumps(item, ensure_ascii=False),
            )
        )

    with connect() as conn:
        conn.execute("DELETE FROM mosques WHERE dataset_id = ?", (dataset_id,))
        conn.executemany(
            """
            INSERT INTO mosques(
                dataset_id, mosque_id, name, address, province, kabko, kecamatan, kelurahan,
                latitude, longitude, rating, review_count, mosque_type, facilities_json,
                capacity_proxy, priority_score, tier, data_quality_json, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.execute(
            "UPDATE datasets SET mosque_count = ?, processed = 1, updated_at = ? WHERE dataset_id = ?",
            (len(records), _now(), dataset_id),
        )


def dataset_has_mosques(dataset_id: str) -> bool:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM mosques WHERE dataset_id = ? LIMIT 1",
            (dataset_id,),
        ).fetchone()
    return row is not None


def get_dataset_profile(dataset_id: str) -> Dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT profile_json FROM datasets WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()
    if not row or not row["profile_json"]:
        return None
    return json.loads(row["profile_json"])


def get_dataset_row(dataset_id: str) -> Dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,)).fetchone()
    return dict(row) if row else None


def _mosque_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return json.loads(row["raw_json"])


def load_mosques(
    dataset_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
    kabko: str | None = None,
    bounds: Tuple[float, float, float, float] | None = None,
) -> List[Dict[str, Any]]:
    init_db()
    where = ["dataset_id = ?"]
    params: List[Any] = [dataset_id]
    if kabko:
        where.append("LOWER(kabko) = LOWER(?)")
        params.append(kabko)
    if bounds:
        south, north, west, east = bounds
        where.append("latitude BETWEEN ? AND ?")
        where.extend(["longitude BETWEEN ? AND ?"])
        params.extend([south, north, west, east])

    sql = f"SELECT raw_json FROM mosques WHERE {' AND '.join(where)} ORDER BY priority_score DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])

    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_mosque_from_row(row) for row in rows]


def get_mosque(dataset_id: str, mosque_id: str) -> Dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT raw_json FROM mosques WHERE dataset_id = ? AND mosque_id = ?",
            (dataset_id, mosque_id),
        ).fetchone()
    return _mosque_from_row(row) if row else None


def count_mosques(dataset_id: str, *, kabko: str | None = None) -> int:
    init_db()
    where = ["dataset_id = ?"]
    params: List[Any] = [dataset_id]
    if kabko:
        where.append("LOWER(kabko) = LOWER(?)")
        params.append(kabko)
    with connect() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS total FROM mosques WHERE {' AND '.join(where)}",
            params,
        ).fetchone()
    return int(row["total"]) if row else 0


def save_osm_graph_cache(
    *,
    graphml_path: Path,
    bounds: Tuple[float, float, float, float],
    buffer_km: float | None,
    network_type: str,
    nodes: int,
    edges: int,
    cache_id: str = "latest",
) -> None:
    init_db()
    south, north, west, east = bounds
    size_mb = round(graphml_path.stat().st_size / (1024 * 1024), 2) if graphml_path.exists() else 0.0
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO osm_graph_cache(
                cache_id, graphml_path, south, north, west, east, buffer_km,
                network_type, nodes, edges, size_mb, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_id) DO UPDATE SET
                graphml_path = excluded.graphml_path,
                south = excluded.south,
                north = excluded.north,
                west = excluded.west,
                east = excluded.east,
                buffer_km = excluded.buffer_km,
                network_type = excluded.network_type,
                nodes = excluded.nodes,
                edges = excluded.edges,
                size_mb = excluded.size_mb,
                updated_at = excluded.updated_at
            """,
            (
                cache_id,
                str(graphml_path),
                south,
                north,
                west,
                east,
                buffer_km,
                network_type,
                int(nodes),
                int(edges),
                size_mb,
                _now(),
            ),
        )


def get_osm_graph_cache(cache_id: str = "latest") -> Dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM osm_graph_cache WHERE cache_id = ?", (cache_id,)).fetchone()
    return dict(row) if row else None
