export type DepartureMode = "now" | "scheduled";

export interface NationalTimeContext {
  iso: string;
  localDate: string;
  localTime: string;
  timeZone: "Asia/Jakarta" | "Asia/Makassar" | "Asia/Jayapura";
  abbreviation: "WIB" | "WITA" | "WIT";
  utcOffset: "+07:00" | "+08:00" | "+09:00";
  cacheKey: string;
}

export function nationalTimeZone(longitude: number): Pick<NationalTimeContext, "timeZone" | "abbreviation" | "utcOffset"> {
  if (longitude >= 126) {
    return { timeZone: "Asia/Jayapura", abbreviation: "WIT", utcOffset: "+09:00" };
  }
  if (longitude >= 110) {
    return { timeZone: "Asia/Makassar", abbreviation: "WITA", utcOffset: "+08:00" };
  }
  return { timeZone: "Asia/Jakarta", abbreviation: "WIB", utcOffset: "+07:00" };
}

function localDateAndTime(now: Date, timeZone: NationalTimeContext["timeZone"]) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return {
    date: `${values.year}-${values.month}-${values.day}`,
    time: `${values.hour}:${values.minute}`,
  };
}

export function buildNationalDepartureTime(
  mode: DepartureMode | string | undefined,
  scheduledTime: string,
  longitude: number,
  now = new Date(),
): NationalTimeContext {
  const zone = nationalTimeZone(longitude);
  const local = localDateAndTime(now, zone.timeZone);
  const useRealtime = mode !== "scheduled";
  const localTime = useRealtime && /^\d{2}:\d{2}$/.test(local.time)
    ? local.time
    : (/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(scheduledTime) ? scheduledTime : local.time);
  const iso = `${local.date}T${localTime}:00${zone.utcOffset}`;
  return {
    ...zone,
    iso,
    localDate: local.date,
    localTime,
    cacheKey: `${local.date}_${localTime}_${zone.abbreviation}`,
  };
}

export function prayerTargetLabel(value: string, nextPrayerName?: string): string {
  if (value === "auto") {
    return nextPrayerName
      ? `Otomatis — ${nextPrayerName} berikutnya`
      : "Otomatis — salat berikutnya";
  }
  return {
    subuh: "Subuh",
    dzuhur: "Dzuhur",
    ashar: "Ashar",
    maghrib: "Maghrib",
    isya: "Isya",
  }[value] || value;
}
