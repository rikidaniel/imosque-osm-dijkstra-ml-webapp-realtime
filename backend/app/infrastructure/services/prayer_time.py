import datetime as dt
import math
import requests
from typing import Dict, Any, Optional

def get_day_of_year(date: dt.date) -> int:
    return date.timetuple().tm_yday

def calculate_offline_prayer_times(lat: float, lon: float, date: dt.date) -> Dict[str, str]:
    """
    Offline calculation of Islamic prayer times for Indonesia region.
    Uses standard astronomical formulas with Indonesian Ministry of Religious Affairs (Kemenag) angles:
    - Subuh (Fajr): -20 degrees
    - Isya (Isha): -18 degrees
    - Dzuhur: Solar transit
    - Ashar: Standard shadow length (Shanti ratio = 1)
    - Maghrib: Sunset (-0.833 degrees)
    """
    # Day of year
    N = get_day_of_year(date)
    
    # Simple declination formula
    declination = math.radians(23.45 * math.sin(math.radians(360.0 / 365.0 * (284 + N))))
    
    # Equation of Time in minutes
    B = math.radians(360.0 / 364.0 * (N - 81))
    eot = 9.87 * math.sin(2.0 * B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)
    
    # Timezone offset: Indonesia is GMT+7 (WIB), GMT+8 (WITA), GMT+9 (WIT)
    if lon < 110.0:
        tz_offset = 7.0
        meridian = 105.0
    elif lon < 126.0:
        tz_offset = 8.0
        meridian = 120.0
    else:
        tz_offset = 9.0
        meridian = 135.0
        
    transit = 12.0 + (meridian - lon) / 15.0 - (eot / 60.0)
    lat_rad = math.radians(lat)
    
    def hour_angle(alt_deg: float) -> Optional[float]:
        alt_rad = math.radians(alt_deg)
        numerator = math.sin(alt_rad) - math.sin(lat_rad) * math.sin(declination)
        denominator = math.cos(lat_rad) * math.cos(declination)
        if abs(denominator) < 1e-6:
            return None
        cos_h = numerator / denominator
        if cos_h < -1.0 or cos_h > 1.0:
            return None
        return math.degrees(math.acos(cos_h)) / 15.0

    h_sunset = hour_angle(-0.833)
    if h_sunset is None:
        h_sunset = 6.0
        
    h_fajr = hour_angle(-20.0)
    if h_fajr is None:
        h_fajr = 6.0
        
    h_isha = hour_angle(-18.0)
    if h_isha is None:
        h_isha = 6.0
        
    delta = abs(lat_rad - declination)
    alt_asr_rad = math.atan(1.0 / (1.0 + math.tan(delta)))
    h_asr = hour_angle(math.degrees(alt_asr_rad))
    if h_asr is None:
        h_asr = 3.0
        
    def format_hour(h_dec: float) -> str:
        h_dec = h_dec % 24
        hours = int(h_dec)
        minutes = int(round((h_dec - hours) * 60))
        if minutes == 60:
            hours = (hours + 1) % 24
            minutes = 0
        return f"{hours:02d}:{minutes:02d}"

    fajr_time = transit - h_fajr
    dhuhr_time = transit + (4.0 / 60.0)
    asr_time = transit + h_asr
    maghrib_time = transit + h_sunset + (2.0 / 60.0)
    isha_time = transit + h_isha
    
    return {
        "fajr": format_hour(fajr_time),
        "dhuhr": format_hour(dhuhr_time),
        "asr": format_hour(asr_time),
        "maghrib": format_hour(maghrib_time),
        "isha": format_hour(isha_time),
    }

from functools import lru_cache

@lru_cache(maxsize=128)
def _get_prayer_times_cached(lat_rounded: float, lon_rounded: float, date_obj: dt.date) -> Dict[str, Any]:
    date_str = date_obj.strftime("%d-%m-%Y")
    try:
        url = f"https://api.aladhan.com/v1/timings/{date_str}"
        params = {
            "latitude": lat_rounded,
            "longitude": lon_rounded,
            "method": 11,
        }
        res = requests.get(url, params=params, timeout=2.0)
        if res.ok:
            data = res.json()
            timings = data.get("data", {}).get("timings", {})
            timezone = data.get("data", {}).get("meta", {}).get("timezone", "Asia/Jakarta")
            if timings:
                return {
                    "source": "api.aladhan.com",
                    "timezone": timezone,
                    "date": date_str,
                    "timings": {
                        "fajr": timings.get("Fajr"),
                        "dhuhr": timings.get("Dhuhr"),
                        "asr": timings.get("Asr"),
                        "maghrib": timings.get("Maghrib"),
                        "isha": timings.get("Isha"),
                    }
                }
    except Exception:
        pass
        
    timezone = "Asia/Jakarta"
    if lon_rounded >= 126.0:
        timezone = "Asia/Jayapura"
    elif lon_rounded >= 110.0:
        timezone = "Asia/Makassar"
        
    offline_times = calculate_offline_prayer_times(lat_rounded, lon_rounded, date_obj)
    
    return {
        "source": "offline_calculation",
        "timezone": timezone,
        "date": date_str,
        "timings": offline_times
    }

def get_prayer_times(lat: float, lon: float, date_obj: dt.date) -> Dict[str, Any]:
    """
    Get prayer times for a specific coordinate and date.
    Tries AlAdhan API first, falls back to offline calculation. Uses caching.
    """
    # Round to 2 decimal places (approx. 1.1 km accuracy) to ensure high cache hit rate
    lat_rounded = round(lat, 2)
    lon_rounded = round(lon, 2)
    return _get_prayer_times_cached(lat_rounded, lon_rounded, date_obj)
