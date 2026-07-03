"use client";

import { useEffect, useState } from "react";
import { useAppStore } from "@/lib/store";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Bell, BellOff, Clock, MapPin, Volume2, VolumeX } from "lucide-react";
import { toast } from "sonner";

interface PrayerSchedule {
  name: string;
  time: string;
  isAlarmActive: boolean;
}

export default function PrayerTimes() {
  const { startPoint } = useAppStore();
  const [schedule, setSchedule] = useState<PrayerSchedule[]>([
    { name: "Subuh", time: "04:45", isAlarmActive: true },
    { name: "Dzuhur", time: "12:02", isAlarmActive: false },
    { name: "Ashar", time: "15:24", isAlarmActive: false },
    { name: "Maghrib", time: "17:58", isAlarmActive: true },
    { name: "Isya", time: "19:12", isAlarmActive: false },
  ]);
  const [hijriDate, setHijriDate] = useState("12 Muharram 1448 H");
  const [masehiDate, setMasehiDate] = useState("12 Juli 2026");
  const [nextPrayer, setNextPrayer] = useState({ name: "Maghrib", countdown: "00:12:45" });
  const [soundEnabled, setSoundEnabled] = useState(true);

  // Fetch real prayer times based on user coordinates if available
  useEffect(() => {
    const fetchPrayerTimes = async () => {
      const lat = startPoint?.lat ?? -6.2088; // Default Jakarta
      const lng = startPoint?.lng ?? 106.8456;
      const date = new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD

      try {
        const res = await fetch(
          `https://api.aladhan.com/v1/timings/${date}?latitude=${lat}&longitude=${lng}&method=20` // Method 20 = Kemenag RI
        );
        if (!res.ok) throw new Error("Gagal mengambil jadwal");
        const data = await res.json();
        const timings = data.data.timings;
        const hijri = data.data.date.hijri;
        const gregorian = data.data.date.gregorian;

        setHijriDate(`${hijri.day} ${hijri.month.en} ${hijri.year} H`);
        setMasehiDate(`${gregorian.day} ${gregorian.month.en} ${gregorian.year}`);

        setSchedule([
          { name: "Subuh", time: timings.Fajr, isAlarmActive: schedule[0].isAlarmActive },
          { name: "Dzuhur", time: timings.Dhuhr, isAlarmActive: schedule[1].isAlarmActive },
          { name: "Ashar", time: timings.Asr, isAlarmActive: schedule[2].isAlarmActive },
          { name: "Maghrib", time: timings.Maghrib, isAlarmActive: schedule[3].isAlarmActive },
          { name: "Isya", time: timings.Isha, isAlarmActive: schedule[4].isAlarmActive },
        ]);
      } catch (err) {
        console.warn("Menggunakan jadwal sholat fallback:", err);
      }
    };

    fetchPrayerTimes();
  }, [startPoint]);

  // Calculate countdown to next prayer
  useEffect(() => {
    const interval = setInterval(() => {
      const now = new Date();
      const currentMinutes = now.getHours() * 60 + now.getMinutes();

      let target: PrayerSchedule | null = null;
      let minDiff = Infinity;

      schedule.forEach((p) => {
        const [h, m] = p.time.split(":").map(Number);
        const prayerMinutes = h * 60 + m;
        let diff = prayerMinutes - currentMinutes;

        // If prayer is tomorrow
        if (diff <= 0) {
          diff += 24 * 60;
        }

        if (diff < minDiff) {
          minDiff = diff;
          target = p;
        }
      });

      if (target) {
        const hours = Math.floor(minDiff / 60);
        const mins = minDiff % 60;
        const secs = 60 - now.getSeconds();
        const countdownStr = `${String(hours).padStart(2, "0")}:${String(mins - 1 >= 0 ? mins - 1 : 59).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
        setNextPrayer({ name: (target as PrayerSchedule).name, countdown: countdownStr });
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [schedule]);

  const toggleAlarm = (index: number) => {
    const updated = [...schedule];
    updated[index].isAlarmActive = !updated[index].isAlarmActive;
    setSchedule(updated);

    const p = updated[index];
    if (p.isAlarmActive) {
      toast.success(`Alarm sholat ${p.name} diaktifkan pada pukul ${p.time}`);
    } else {
      toast.info(`Alarm sholat ${p.name} dinonaktifkan`);
    }
  };

  return (
    <div className="space-y-6">
      {/* Hijriah card */}
      <div className="relative p-5 rounded-2xl bg-gradient-to-br from-emerald-700 to-teal-900 text-white shadow-lg overflow-hidden">
        {/* Background glow */}
        <div className="absolute -right-10 -bottom-10 w-32 h-32 bg-emerald-500/20 rounded-full blur-2xl"></div>
        <div className="relative z-10 flex justify-between items-start">
          <div>
            <span className="text-[10px] uppercase font-bold text-emerald-200 tracking-wider">Kalender Islam</span>
            <h3 className="text-lg font-bold tracking-tight mt-1">{hijriDate}</h3>
            <p className="text-xs text-emerald-100/80 mt-0.5">{masehiDate}</p>
          </div>
          {startPoint && (
            <div className="flex items-center gap-1 bg-emerald-800/40 text-emerald-200 px-2.5 py-1 rounded-full text-[10px] font-bold border border-emerald-600/30">
              <MapPin className="w-3 h-3" />
              GPS Aktif
            </div>
          )}
        </div>

        {/* Countdown Area */}
        <div className="mt-6 pt-4 border-t border-emerald-600/30 flex items-center justify-between">
          <div>
            <span className="text-[10px] text-emerald-200 font-medium block">Sholat Berikutnya</span>
            <span className="text-sm font-bold text-white">{nextPrayer.name}</span>
          </div>
          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-emerald-300 animate-pulse" />
            <span className="text-xl font-mono font-black tracking-wider text-emerald-200">{nextPrayer.countdown}</span>
          </div>
        </div>
      </div>

      {/* Prayer Schedule Card */}
      <Card className="border-slate-100 shadow-sm rounded-2xl">
        <CardHeader className="pb-3 flex flex-row items-center justify-between">
          <div>
            <CardTitle className="text-base font-bold">Jadwal Sholat Harian</CardTitle>
            <CardDescription className="text-xs">Aktifkan notifikasi adzan di sini.</CardDescription>
          </div>
          <button
            onClick={() => {
              setSoundEnabled(!soundEnabled);
              toast.success(soundEnabled ? "Suara alarm dimatikan (mode getar)" : "Suara alarm diaktifkan (suara adzan)");
            }}
            className="p-2 rounded-xl bg-slate-50 border hover:bg-slate-100 transition-colors text-slate-600"
            title={soundEnabled ? "Matikan Suara" : "Aktifkan Suara"}
          >
            {soundEnabled ? <Volume2 className="w-4 h-4 text-emerald-600" /> : <VolumeX className="w-4 h-4 text-slate-400" />}
          </button>
        </CardHeader>
        <CardContent className="p-0">
          <div className="divide-y divide-slate-100">
            {schedule.map((p, idx) => (
              <div
                key={p.name}
                className="flex items-center justify-between px-6 py-4 hover:bg-slate-50/50 transition-colors"
              >
                <div className="flex items-center gap-3">
                  <div className={`w-2.5 h-2.5 rounded-full ${p.name === nextPrayer.name ? "bg-emerald-500 animate-ping" : "bg-slate-200"}`}></div>
                  <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">{p.name}</span>
                </div>
                <div className="flex items-center gap-6">
                  <span className="font-mono text-sm font-bold text-slate-800 dark:text-slate-200">{p.time}</span>
                  <button
                    onClick={() => toggleAlarm(idx)}
                    className={`p-2 rounded-lg border transition-all duration-300 ${
                      p.isAlarmActive
                        ? "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-600 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800/40"
                        : "bg-white dark:bg-slate-950 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 border-slate-200 dark:border-slate-800/80"
                    }`}
                  >
                    {p.isAlarmActive ? <Bell className="w-4 h-4 fill-emerald-600/10" /> : <BellOff className="w-4 h-4" />}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
