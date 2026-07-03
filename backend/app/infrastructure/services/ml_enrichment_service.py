import pandas as pd
import numpy as np
import re
import math
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

try:
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.multiclass import OneVsRestClassifier
    from sklearn.preprocessing import MultiLabelBinarizer
except Exception:
    RandomForestRegressor = None
    TfidfVectorizer = None
    LogisticRegression = None
    OneVsRestClassifier = None
    MultiLabelBinarizer = None

INDONESIA_LAT_RANGE = (-11.5, 6.5)
INDONESIA_LON_RANGE = (94.0, 142.5)

FACILITY_LABELS = [
    "parking", "wudu_area", "toilet", "women_area",
    "ac", "sound_system", "wifi", "canteen", "library",
]

def slugify_dataset_name(name: str) -> str:
    text = str(name or "dataset").lower().strip()
    text = re.sub(r"\.[a-z0-9]+$", "", text)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "dataset"

def _fix_missing_decimal(val: float, lo: float, hi: float) -> float:
    """Perbaiki koordinat yang kehilangan pemisah desimal saat ekspor.
    
    Contoh: -71421019 (seharusnya -7.1421019) → bagi dengan 10^n sampai
    masuk range yang diharapkan (misal lat Indonesia: -11.5 s/d 6.5).
    """
    if np.isnan(val):
        return val
    if lo <= val <= hi:
        return val
    for exp in range(1, 10):
        candidate = val / (10 ** exp)
        if lo <= candidate <= hi:
            return candidate
    return val  # kembalikan asli jika tidak bisa diperbaiki

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
        "wudhu_area": "wudu_area", "wudu": "wudu_area", "tempat_wudhu": "wudu_area",
        "toilets": "toilet", "woman_area": "women_area", "sound": "sound_system",
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

class MLEnrichmentService:
    @staticmethod
    def clean_and_enrich(df_raw: pd.DataFrame, dataset_id: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        df = df_raw.copy()
        df.columns = [str(c).strip().lower() for c in df.columns]

        required = ["latitude", "longitude", "name"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Kolom wajib tidak ditemukan: {missing}.")

        df["latitude"] = df["latitude"].map(_parse_decimal)
        df["longitude"] = df["longitude"].map(_parse_decimal)

        # Debug logging untuk troubleshoot
        valid_lat_count = df["latitude"].notna().sum()
        valid_lon_count = df["longitude"].notna().sum()
        logger.info(f"Parsed koordinat: {valid_lat_count}/{len(df)} lat valid, {valid_lon_count}/{len(df)} lon valid")
        if len(df) > 0:
            logger.info(f"Contoh lat (5 pertama): {df['latitude'].head(5).tolist()}")
            logger.info(f"Contoh lon (5 pertama): {df['longitude'].head(5).tolist()}")

        # Auto-fix: koordinat tanpa pemisah desimal (misal -71421019 → -7.1421019)
        # Ini terjadi saat Google Sheets/Excel mengekspor dengan locale koma, lalu koma hilang
        initial_valid = (
            df["latitude"].between(*INDONESIA_LAT_RANGE) &
            df["longitude"].between(*INDONESIA_LON_RANGE)
        )
        if initial_valid.sum() == 0 and valid_lat_count > 0:
            logger.info("Tidak ada koordinat dalam range — mencoba auto-fix desimal yang hilang...")
            df["latitude"] = df["latitude"].map(lambda v: _fix_missing_decimal(v, *INDONESIA_LAT_RANGE))
            df["longitude"] = df["longitude"].map(lambda v: _fix_missing_decimal(v, *INDONESIA_LON_RANGE))
            fixed_valid = (
                df["latitude"].between(*INDONESIA_LAT_RANGE) &
                df["longitude"].between(*INDONESIA_LON_RANGE)
            ).sum()
            logger.info(f"Setelah auto-fix desimal: {fixed_valid}/{len(df)} koordinat valid")
            if fixed_valid > 0 and len(df) > 0:
                logger.info(f"Contoh lat setelah fix: {df['latitude'].head(5).tolist()}")
                logger.info(f"Contoh lon setelah fix: {df['longitude'].head(5).tolist()}")

        df["rating_numeric"] = df.get("rating", pd.Series([np.nan]*len(df))).map(_parse_decimal)
        df["review_count_numeric"] = df.get("review_count", pd.Series([0]*len(df))).map(_parse_int)
        df["checkin_count_numeric"] = df.get("checkin_count", pd.Series([0]*len(df))).map(_parse_int)

        valid_coord = (
            df["latitude"].between(*INDONESIA_LAT_RANGE) &
            df["longitude"].between(*INDONESIA_LON_RANGE)
        )
        logger.info(f"Koordinat valid di range Indonesia: {valid_coord.sum()}/{len(df)}")
        
        # Coba auto-swap jika tidak ada yang valid (seringkali user terbalik memasukkan x dan y)
        if valid_coord.sum() == 0:
            swapped_valid_coord = (
                df["longitude"].between(*INDONESIA_LAT_RANGE) &
                df["latitude"].between(*INDONESIA_LON_RANGE)
            )
            if swapped_valid_coord.sum() > 0:
                logger.info(f"Auto-swap lat/lon: {swapped_valid_coord.sum()} baris valid setelah swap")
                # Gunakan .copy() agar swap aman di semua versi pandas
                lat_copy = df["latitude"].copy()
                lon_copy = df["longitude"].copy()
                df["latitude"] = lon_copy
                df["longitude"] = lat_copy
                valid_coord = swapped_valid_coord

        df = df[valid_coord].copy()

        if len(df) == 0:
            sample_lat = df_raw["latitude"].head(3).tolist() if "latitude" in df_raw.columns else []
            sample_lon = df_raw["longitude"].head(3).tolist() if "longitude" in df_raw.columns else []
            raise ValueError(f"Tidak ada koordinat valid di wilayah Indonesia. (Lat: -11.5 s/d 6.5, Lon: 94 s/d 142.5). Contoh input Anda - Lat: {sample_lat}, Lon: {sample_lon}")

        id_col = "uuid (primary_key)" if "uuid (primary_key)" in df.columns else None
        if id_col:
            df = df.drop_duplicates(subset=[id_col], keep="first")
        else:
            df = df.drop_duplicates(subset=["name", "latitude", "longitude"], keep="first")
            df["uuid (primary_key)"] = [f"{dataset_id}_{i}" for i in range(len(df))]

        text_cols = ["name", "address", "provinsi", "kabko", "kecamatan", "kelurahan", "mosque_type", "facilities"]
        for col in text_cols:
            if col not in df.columns:
                df[col] = ""
            df[col] = df[col].fillna("").astype(str).str.strip()

        df["facilities_list_original"] = df["facilities"].map(_split_facilities)
        df["has_original_facilities"] = df["facilities_list_original"].map(lambda x: len(x) > 0)
        df["has_original_rating"] = df["rating_numeric"].notna()

        dataset_label = df["provinsi"].mode().iloc[0] if len(df["provinsi"].mode()) else dataset_id.replace("_", " ").title()

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

        df["text_for_facilities"] = (
            df["name"] + " " + df["address"] + " " + df["provinsi"] + " " + df["kabko"] + " " +
            df["kecamatan"] + " " + df["mosque_type"]
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
            0.35 * rating_norm + 0.20 * reviews_norm + 0.20 * facilities_norm + 0.15 * capacity_num + 0.10 * type_bonus
        ).clip(0, 1).round(4)

        df["tier"] = df["priority_score"].map(lambda s: "A" if s >= 0.78 else ("B" if s >= 0.58 else ("C" if s >= 0.38 else "D")))

        records = []
        for _, row in df.iterrows():
            records.append({
                "id": str(row["uuid (primary_key)"]),
                "name": row.get("name", ""),
                "address": row.get("address", ""),
                "province": row.get("provinsi", dataset_label),
                "provinsi": row.get("provinsi", dataset_label),
                "kabko": row.get("kabko", ""),
                "kecamatan": row.get("kecamatan", ""),
                "kelurahan": row.get("kelurahan", ""),
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "rating": float(row["rating_final"]),
                "review_count": int(row["review_count_numeric"]),
                "mosque_type": row.get("mosque_type", ""),
                "facilities": row["facilities_final"],
                "fasilitas": ", ".join(row["facilities_final"]),
                "capacity_proxy": row["capacity_proxy"],
                "priority_score": float(row["priority_score"]),
                "tier": row["tier"],
                "data_quality": {
                    "coordinate_source": "original_dataset",
                    "rating_source": row["rating_source"],
                    "facilities_source": row["facilities_source"],
                    "capacity_source": row["capacity_source"],
                },
            })

        profile = {
            "dataset_id": dataset_id,
            "dataset_label": dataset_label,
            "raw_rows": int(len(df_raw)),
            "valid_coordinate_rows": int(len(df)),
            "enriched_rows": len(records),
            "bbox": {
                "south": float(df["latitude"].min()),
                "north": float(df["latitude"].max()),
                "west": float(df["longitude"].min()),
                "east": float(df["longitude"].max()),
            } if len(df) else None
        }

        return records, profile
