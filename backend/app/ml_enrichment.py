from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from . import local_db

try:
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.multiclass import OneVsRestClassifier
    from sklearn.preprocessing import MultiLabelBinarizer
except Exception:  # pragma: no cover
    RandomForestRegressor = None
    TfidfVectorizer = None
    LogisticRegression = None
    OneVsRestClassifier = None
    MultiLabelBinarizer = None

PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
DATASETS_DIR = RAW_DIR / "datasets"
PROCESSED_DIR = DATA_DIR / "processed"
ACTIVE_DATASET_JSON = PROCESSED_DIR / "active_dataset.json"
LEGACY_RAW_CSV = RAW_DIR / "dataset_masjid_banten.csv"
DEFAULT_DATASET_ID = "banten"

# Backward-compatible paths for older parts of the project/readme.
RAW_CSV = LEGACY_RAW_CSV
ENRICHED_JSON = PROCESSED_DIR / DEFAULT_DATASET_ID / "enriched_mosques.json"
PROFILE_JSON = PROCESSED_DIR / DEFAULT_DATASET_ID / "data_profile_summary.json"

# General Indonesia coordinate bounds. This replaces the old Banten-only filter
# so the app can switch to DKI Jakarta, Jawa Barat, Jawa Tengah, Jawa Timur, etc.
INDONESIA_LAT_RANGE = (-11.5, 6.5)
INDONESIA_LON_RANGE = (94.0, 142.5)


PROVINCE_BOUNDS = {
    # Bounds are intentionally a little wider than administrative borders to tolerate imperfect geocoding.
    "BANTEN": {"south": -7.4, "north": -5.6, "west": 105.0, "east": 107.5},
    "DKI JAKARTA": {"south": -6.45, "north": -5.90, "west": 106.55, "east": 107.10},
    "JAWA BARAT": {"south": -7.95, "north": -5.75, "west": 105.80, "east": 109.10},
    "JAWA TENGAH": {"south": -8.35, "north": -5.55, "west": 108.35, "east": 111.90},
    "JAWA TIMUR": {"south": -9.30, "north": -5.00, "west": 110.75, "east": 116.90},
    "DI YOGYAKARTA": {"south": -8.25, "north": -7.45, "west": 109.95, "east": 110.95},
    "DAERAH ISTIMEWA YOGYAKARTA": {"south": -8.25, "north": -7.45, "west": 109.95, "east": 110.95},
}

FACILITY_LABELS = [
    "parking",
    "wudu_area",
    "toilet",
    "women_area",
    "ac",
    "sound_system",
    "wifi",
    "canteen",
    "library",
]


def slugify_dataset_name(name: str) -> str:
    text = str(name or "dataset").lower().strip()
    text = re.sub(r"\.[a-z0-9]+$", "", text)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "dataset"


def ensure_default_dataset() -> None:
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    default_csv = DATASETS_DIR / f"{DEFAULT_DATASET_ID}.csv"
    if not default_csv.exists() and LEGACY_RAW_CSV.exists():
        shutil.copy2(LEGACY_RAW_CSV, default_csv)
    if not ACTIVE_DATASET_JSON.exists():
        set_active_dataset_id(DEFAULT_DATASET_ID if default_csv.exists() else "")


def dataset_paths(dataset_id: str | None = None) -> Dict[str, Path]:
    ensure_default_dataset()
    did = dataset_id or get_active_dataset_id() or DEFAULT_DATASET_ID
    did = slugify_dataset_name(did)
    processed = PROCESSED_DIR / did
    return {
        "dataset_id": Path(did),
        "raw_csv": DATASETS_DIR / f"{did}.csv",
        "processed_dir": processed,
        "enriched_json": processed / "enriched_mosques.json",
        "profile_json": processed / "data_profile_summary.json",
    }


def get_active_dataset_id() -> str:
    ensure_default_dataset()
    if ACTIVE_DATASET_JSON.exists():
        try:
            data = json.loads(ACTIVE_DATASET_JSON.read_text(encoding="utf-8"))
            return slugify_dataset_name(data.get("active_dataset_id", DEFAULT_DATASET_ID))
        except Exception:
            return DEFAULT_DATASET_ID
    return DEFAULT_DATASET_ID


def set_active_dataset_id(dataset_id: str) -> Dict[str, Any]:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    did = slugify_dataset_name(dataset_id)
    ACTIVE_DATASET_JSON.write_text(
        json.dumps({"active_dataset_id": did}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    local_db.set_setting("active_dataset_id", did)
    return {"active_dataset_id": did}


def list_datasets() -> List[Dict[str, Any]]:
    ensure_default_dataset()
    active = get_active_dataset_id()
    items: List[Dict[str, Any]] = []
    for csv_path in sorted(DATASETS_DIR.glob("*.csv")):
        did = slugify_dataset_name(csv_path.stem)
        paths = dataset_paths(did)
        profile = local_db.get_dataset_profile(did)
        if paths["profile_json"].exists():
            try:
                profile = profile or json.loads(paths["profile_json"].read_text(encoding="utf-8"))
            except Exception:
                profile = profile or None
        db_row = local_db.get_dataset_row(did)
        local_db.upsert_dataset(
            did,
            filename=csv_path.name,
            raw_csv_path=str(csv_path),
            enriched_json_path=str(paths["enriched_json"]),
            processed=local_db.dataset_has_mosques(did) or paths["enriched_json"].exists(),
            profile=profile,
            mosque_count=int(db_row["mosque_count"]) if db_row else 0,
        )
        items.append({
            "dataset_id": did,
            "filename": csv_path.name,
            "raw_csv_path": str(csv_path),
            "is_active": did == active,
            "processed": local_db.dataset_has_mosques(did) or paths["enriched_json"].exists(),
            "enriched_json_path": str(paths["enriched_json"]),
            "profile": profile,
        })
    return items


def save_uploaded_dataset(file_bytes: bytes, filename: str, dataset_name: str | None = None, make_active: bool = True) -> Dict[str, Any]:
    ensure_default_dataset()
    base_name = dataset_name or filename or "dataset"
    did = slugify_dataset_name(base_name)
    csv_path = DATASETS_DIR / f"{did}.csv"
    csv_path.write_bytes(file_bytes)
    if make_active:
        set_active_dataset_id(did)
    local_db.upsert_dataset(
        did,
        filename=filename,
        raw_csv_path=str(csv_path),
        processed=False,
    )
    return {
        "dataset_id": did,
        "filename": filename,
        "raw_csv_path": str(csv_path),
        "is_active": make_active,
    }



def _dataset_specific_bounds(df: pd.DataFrame) -> Dict[str, float] | None:
    if "provinsi" not in df.columns:
        return None
    values = df["provinsi"].dropna().astype(str).str.upper().str.strip()
    if values.empty:
        return None
    province = values.mode().iloc[0]
    return PROVINCE_BOUNDS.get(province)


def _apply_robust_coordinate_filter(df: pd.DataFrame) -> pd.DataFrame:
    if len(df) < 50:
        return df
    lat_q1, lat_q3 = df["latitude"].quantile([0.01, 0.99])
    lon_q1, lon_q3 = df["longitude"].quantile([0.01, 0.99])
    lat_pad = max(0.10, (lat_q3 - lat_q1) * 0.15)
    lon_pad = max(0.10, (lon_q3 - lon_q1) * 0.15)
    return df[
        df["latitude"].between(lat_q1 - lat_pad, lat_q3 + lat_pad)
        & df["longitude"].between(lon_q1 - lon_pad, lon_q3 + lon_pad)
    ].copy()

def _read_csv_flexible(raw_csv: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(raw_csv, sep=None, engine="python")
    except Exception:
        return pd.read_csv(raw_csv)


def _parse_decimal(value: Any) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return np.nan
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return np.nan
    text = text.replace(".", "") if re.match(r"^-?\d{1,3}(\.\d{3})+,\d+", text) else text
    text = text.replace(",", ".")
    text = re.sub(r"[^0-9.\-]", "", text)
    try:
        return float(text)
    except ValueError:
        return np.nan


def _parse_int(value: Any) -> int:
    number = _parse_decimal(value)
    if np.isnan(number):
        return 0
    return int(round(number))


def _split_facilities(value: Any) -> List[str]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    text = str(value).strip().lower()
    if not text or text in {"nan", "none", "null"}:
        return []
    parts = re.split(r"[|,;/]+", text)
    cleaned = []
    aliases = {
        "wudhu_area": "wudu_area",
        "wudu": "wudu_area",
        "tempat_wudhu": "wudu_area",
        "toilets": "toilet",
        "woman_area": "women_area",
        "sound": "sound_system",
    }
    for part in parts:
        item = re.sub(r"\s+", "_", part.strip())
        item = aliases.get(item, item)
        if item:
            cleaned.append(item)
    return sorted(set(cleaned))


def _normalise(series: pd.Series, reverse: bool = False) -> pd.Series:
    s = series.astype(float)
    mn, mx = s.min(), s.max()
    if pd.isna(mn) or pd.isna(mx) or mx == mn:
        out = pd.Series(np.zeros(len(s)), index=s.index)
    else:
        out = (s - mn) / (mx - mn)
    return 1 - out if reverse else out


def _col(df: pd.DataFrame, name: str, default: Any = "") -> pd.Series:
    return df[name] if name in df.columns else pd.Series([default] * len(df), index=df.index)


def _infer_dataset_label(dataset_id: str, df: pd.DataFrame) -> str:
    if "provinsi" in df.columns and df["provinsi"].notna().any():
        mode = df["provinsi"].dropna().astype(str).str.strip()
        if len(mode):
            return str(mode.mode().iloc[0])
    return dataset_id.replace("_", " ").title()


def load_and_clean(raw_csv: Path | None = None, dataset_id: str | None = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    if raw_csv is None:
        raw_csv = dataset_paths(dataset_id)["raw_csv"]
    if not Path(raw_csv).exists():
        raise FileNotFoundError(f"Dataset CSV tidak ditemukan: {raw_csv}")

    df_raw = _read_csv_flexible(Path(raw_csv))
    df_raw.columns = [str(c).strip().lower() for c in df_raw.columns]
    df = df_raw.copy()

    required = ["latitude", "longitude", "name"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Kolom wajib tidak ditemukan: {missing}. Pastikan CSV punya kolom latitude, longitude, dan name.")

    df["latitude"] = df["latitude"].map(_parse_decimal)
    df["longitude"] = df["longitude"].map(_parse_decimal)
    df["rating_numeric"] = _col(df, "rating", np.nan).map(_parse_decimal)
    df["review_count_numeric"] = _col(df, "review_count", 0).map(_parse_int)
    df["checkin_count_numeric"] = _col(df, "checkin_count", 0).map(_parse_int)

    valid_coord = (
        df["latitude"].between(*INDONESIA_LAT_RANGE)
        & df["longitude"].between(*INDONESIA_LON_RANGE)
    )
    df = df[valid_coord].copy()

    bounds = _dataset_specific_bounds(df)
    if bounds:
        before_bounds = len(df)
        df = df[
            df["latitude"].between(bounds["south"], bounds["north"])
            & df["longitude"].between(bounds["west"], bounds["east"])
        ].copy()
        coordinate_filter_note = f"province_bounds_filter_removed_{before_bounds - len(df)}"
    else:
        before_bounds = len(df)
        df = _apply_robust_coordinate_filter(df)
        coordinate_filter_note = f"robust_outlier_filter_removed_{before_bounds - len(df)}"

    id_col = "uuid (primary_key)" if "uuid (primary_key)" in df.columns else None
    if id_col:
        df = df.drop_duplicates(subset=[id_col], keep="first")
    else:
        df = df.drop_duplicates(subset=["name", "latitude", "longitude"], keep="first")
        df["uuid (primary_key)"] = [f"{slugify_dataset_name(dataset_id or 'dataset')}_{i}" for i in range(len(df))]

    text_cols = ["name", "address", "provinsi", "kabko", "kecamatan", "kelurahan", "mosque_type", "mosque_topology", "facilities"]
    for col in text_cols:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str).str.strip()

    df["facilities_list_original"] = df["facilities"].map(_split_facilities)
    df["has_original_facilities"] = df["facilities_list_original"].map(lambda x: len(x) > 0)
    df["has_original_rating"] = df["rating_numeric"].notna()

    bbox = None
    if len(df):
        bbox = {
            "south": float(df["latitude"].min()),
            "north": float(df["latitude"].max()),
            "west": float(df["longitude"].min()),
            "east": float(df["longitude"].max()),
        }

    profile = {
        "dataset_id": slugify_dataset_name(dataset_id or Path(raw_csv).stem),
        "source_csv": str(raw_csv),
        "raw_rows": int(len(df_raw)),
        "valid_coordinate_rows": int(len(df)),
        "removed_rows_by_coordinate_or_duplicate": int(len(df_raw) - len(df)),
        "coordinate_filter_note": coordinate_filter_note,
        "original_rating_available": int(df["has_original_rating"].sum()),
        "original_facilities_available": int(df["has_original_facilities"].sum()),
        "bbox": bbox,
        "province_counts": df["provinsi"].value_counts().head(10).to_dict() if "provinsi" in df.columns else {},
        "kabko_counts": df["kabko"].value_counts().head(20).to_dict() if "kabko" in df.columns else {},
    }
    return df.reset_index(drop=True), profile


def enrich_dataset(
    dataset_id: str | None = None,
    raw_csv: Path | None = None,
    out_json: Path | None = None,
    profile_json: Path | None = None,
) -> Dict[str, Any]:
    ensure_default_dataset()
    did = slugify_dataset_name(dataset_id or get_active_dataset_id() or DEFAULT_DATASET_ID)
    paths = dataset_paths(did)
    raw_csv = raw_csv or paths["raw_csv"]
    out_json = out_json or paths["enriched_json"]
    profile_json = profile_json or paths["profile_json"]
    paths["processed_dir"].mkdir(parents=True, exist_ok=True)

    df, profile = load_and_clean(raw_csv, dataset_id=did)
    dataset_label = _infer_dataset_label(did, df)

    if len(df) == 0:
        raise ValueError("Tidak ada baris valid setelah cleaning koordinat. Cek format latitude/longitude dataset.")

    # 1) ML regression for missing rating.
    df["rating_final"] = df["rating_numeric"]
    df["rating_source"] = np.where(df["rating_numeric"].notna(), "original", "ml_prediction")

    feature_cols = ["latitude", "longitude", "review_count_numeric", "checkin_count_numeric", "provinsi", "kabko", "kecamatan", "mosque_type"]
    train_mask = df["rating_numeric"].notna()
    if RandomForestRegressor is not None and train_mask.sum() >= 20 and (~train_mask).sum() > 0:
        X = pd.get_dummies(df[feature_cols], columns=["provinsi", "kabko", "kecamatan", "mosque_type"], dummy_na=True)
        y = df.loc[train_mask, "rating_numeric"].clip(1, 5)
        model = RandomForestRegressor(n_estimators=120, random_state=42, min_samples_leaf=3, n_jobs=-1)
        model.fit(X.loc[train_mask], y)
        pred = model.predict(X.loc[~train_mask])
        df.loc[~train_mask, "rating_final"] = np.clip(pred, 1, 5)
    else:
        median_rating = float(df["rating_numeric"].median()) if df["rating_numeric"].notna().any() else 4.0
        df.loc[~train_mask, "rating_final"] = median_rating
        df.loc[~train_mask, "rating_source"] = "median_imputation"

    df["rating_final"] = df["rating_final"].fillna(df["rating_final"].median()).clip(1, 5).round(2)

    # 2) ML multi-label text classifier for missing facilities.
    df["text_for_facilities"] = (
        df["name"].fillna("") + " " + df["address"].fillna("") + " " +
        df["provinsi"].fillna("") + " " + df["kabko"].fillna("") + " " +
        df["kecamatan"].fillna("") + " " + df["mosque_type"].fillna("")
    ).str.lower()

    df["facilities_final"] = df["facilities_list_original"].map(list)
    df["facilities_source"] = np.where(df["has_original_facilities"], "original", "ml_prediction")

    train_fac_mask = df["has_original_facilities"]
    if TfidfVectorizer is not None and train_fac_mask.sum() >= 30 and (~train_fac_mask).sum() > 0:
        mlb = MultiLabelBinarizer(classes=FACILITY_LABELS)
        Y = mlb.fit_transform(df.loc[train_fac_mask, "facilities_list_original"])
        vectorizer = TfidfVectorizer(max_features=4000, ngram_range=(1, 2), min_df=2)
        X_text = vectorizer.fit_transform(df.loc[train_fac_mask, "text_for_facilities"])
        clf = OneVsRestClassifier(LogisticRegression(max_iter=1000, class_weight="balanced"))
        clf.fit(X_text, Y)
        X_missing = vectorizer.transform(df.loc[~train_fac_mask, "text_for_facilities"])
        pred = clf.predict(X_missing)
        predicted_lists = mlb.inverse_transform(pred)
        missing_idx = df.index[~train_fac_mask]
        for idx, labels in zip(missing_idx, predicted_lists):
            labels = sorted(set(labels))
            if not labels:
                labels = ["parking"] if df.at[idx, "review_count_numeric"] >= 10 else ["wudu_area"]
            df.at[idx, "facilities_final"] = labels
    else:
        missing_idx = df.index[~train_fac_mask]
        for idx in missing_idx:
            df.at[idx, "facilities_final"] = ["parking"] if df.at[idx, "review_count_numeric"] >= 10 else ["wudu_area"]
            df.at[idx, "facilities_source"] = "rule_based_prediction"

    # 3) Capacity proxy and priority scoring.
    def capacity_proxy(row: pd.Series) -> str:
        name_type = f"{row.get('name', '')} {row.get('mosque_type', '')}".lower()
        facilities = set(row["facilities_final"])
        reviews = row["review_count_numeric"]
        rating = row["rating_final"]
        if "islamic_center" in name_type or "raya" in name_type or reviews >= 80 or len(facilities) >= 5:
            return "large"
        if reviews >= 15 or rating >= 4.5 or len(facilities) >= 3:
            return "medium"
        return "small"

    df["capacity_proxy"] = df.apply(capacity_proxy, axis=1)
    df["capacity_source"] = "proxy_estimation"

    rating_norm = (df["rating_final"].fillna(4.0) - 1) / 4
    reviews_norm = _normalise(np.log1p(df["review_count_numeric"].fillna(0)))
    facilities_norm = _normalise(df["facilities_final"].map(len))
    type_bonus = df["mosque_type"].str.lower().map(lambda x: 1.0 if "islamic" in x or "masjid" in x else 0.6)
    capacity_num = df["capacity_proxy"].map({"large": 1.0, "medium": 0.65, "small": 0.35}).fillna(0.5)

    df["priority_score"] = (
        0.35 * rating_norm +
        0.20 * reviews_norm +
        0.20 * facilities_norm +
        0.15 * capacity_num +
        0.10 * type_bonus
    ).clip(0, 1).round(4)

    def tier(score: float) -> str:
        if score >= 0.78:
            return "A"
        if score >= 0.58:
            return "B"
        if score >= 0.38:
            return "C"
        return "D"

    df["tier"] = df["priority_score"].map(tier)

    records: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        records.append({
            "id": str(row["uuid (primary_key)"]),
            "name": row.get("name", ""),
            "address": row.get("address", ""),
            "province": row.get("provinsi", dataset_label),
            "kabko": row.get("kabko", ""),
            "kecamatan": row.get("kecamatan", ""),
            "kelurahan": row.get("kelurahan", ""),
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "rating": float(row["rating_final"]),
            "review_count": int(row["review_count_numeric"]),
            "mosque_type": row.get("mosque_type", ""),
            "facilities": row["facilities_final"],
            "capacity_proxy": row["capacity_proxy"],
            "priority_score": float(row["priority_score"]),
            "tier": row["tier"],
            "dataset_id": did,
            "data_quality": {
                "coordinate_source": "original_dataset",
                "rating_source": row["rating_source"],
                "facilities_source": row["facilities_source"],
                "capacity_source": row["capacity_source"],
                "note": "ML/proxy fields are estimates for routing support, not verified field facts.",
            },
        })

    with Path(out_json).open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    profile.update({
        "dataset_id": did,
        "dataset_label": dataset_label,
        "enriched_rows": len(records),
        "rating_ml_predicted": int((df["rating_source"] == "ml_prediction").sum()),
        "facilities_ml_predicted": int((df["facilities_source"] == "ml_prediction").sum()),
        "capacity_proxy_rows": int(len(records)),
        "output_json": str(out_json),
    })
    with Path(profile_json).open("w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

    local_db.save_mosques(
        did,
        records,
        profile=profile,
        raw_csv_path=str(raw_csv),
        enriched_json_path=str(out_json),
    )
    set_active_dataset_id(did)
    return profile


def load_enriched_mosques(dataset_id: str | None = None, json_path: Path | None = None) -> List[Dict[str, Any]]:
    ensure_default_dataset()
    did = slugify_dataset_name(dataset_id or get_active_dataset_id() or DEFAULT_DATASET_ID)
    if json_path is None and local_db.dataset_has_mosques(did):
        return local_db.load_mosques(did)
    if json_path is None:
        json_path = dataset_paths(did)["enriched_json"]
    if not Path(json_path).exists():
        enrich_dataset(did)
    with Path(json_path).open("r", encoding="utf-8") as f:
        records = json.load(f)
    profile = local_db.get_dataset_profile(did)
    profile_path = dataset_paths(did)["profile_json"]
    if profile is None and profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            profile = {}
    if json_path is None or did == slugify_dataset_name(dataset_id or did):
        local_db.save_mosques(
            did,
            records,
            profile=profile or {"dataset_id": did, "enriched_rows": len(records)},
            raw_csv_path=str(dataset_paths(did)["raw_csv"]),
            enriched_json_path=str(json_path),
        )
    return records


if __name__ == "__main__":
    print(json.dumps(enrich_dataset(), indent=2, ensure_ascii=False))
