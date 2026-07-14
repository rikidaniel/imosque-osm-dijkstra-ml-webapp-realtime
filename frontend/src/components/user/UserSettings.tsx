"use client";

import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Shield, Settings, Volume2, Info } from "lucide-react";
import { useState, useEffect } from "react";
import { toast } from "sonner";

export default function UserSettings() {
  const [mounted, setMounted] = useState(false);
  const [soundOption, setSoundOption] = useState("makkah");
  const [highAccuracy, setHighAccuracy] = useState(true);

  // Avoid hydration mismatch
  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) return null;

  return (
    <div className="space-y-6">
      {/* App Preferences */}
      <Card className="border-slate-100 shadow-sm rounded-2xl">
        <CardHeader>
          <CardTitle className="text-base font-bold flex items-center gap-2">
            <Settings className="w-4 h-4 text-emerald-600" />
            Preferensi Aplikasi
          </CardTitle>
          <CardDescription className="text-xs">Sesuaikan kebutuhan navigasi & ibadah Anda.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Adzan Sound */}
          <div className="space-y-2">
            <div className="flex flex-col gap-0.5">
              <span className="text-sm font-semibold text-slate-700">Nada Alarm Adzan</span>
              <span className="text-[11px] text-slate-500">Pilih suara adzan untuk alarm sholat</span>
            </div>
            <Select value={soundOption} onValueChange={(val) => {
              if (val) {
                setSoundOption(val);
                toast.success(`Suara alarm diganti ke: Adzan ${val.charAt(0).toUpperCase() + val.slice(1)}`);
              }
            }}>
              <SelectTrigger className="w-full bg-slate-50">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="makkah">Adzan Makkah (Merdu)</SelectItem>
                <SelectItem value="madinah">Adzan Madinah (Syahdu)</SelectItem>
                <SelectItem value="indonesia">Adzan Standar Indonesia</SelectItem>
                <SelectItem value="beep">Nada Beep Sederhana</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Location Accuracy Toggle */}
          <div className="flex items-center justify-between pt-3 border-t border-slate-100">
            <div className="flex flex-col gap-0.5">
              <span className="text-sm font-semibold text-slate-700">GPS Akurasi Tinggi</span>
              <span className="text-[11px] text-slate-500">Meningkatkan presisi deteksi lokasi</span>
            </div>
            <button
              onClick={() => {
                setHighAccuracy(!highAccuracy);
                toast.success(highAccuracy ? "Akurasi GPS diturunkan (hemat baterai)" : "Akurasi GPS tinggi diaktifkan");
              }}
              className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                highAccuracy ? "bg-emerald-600" : "bg-slate-200"
              }`}
            >
              <span
                className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                  highAccuracy ? "translate-x-5" : "translate-x-0"
                }`}
              />
            </button>
          </div>
        </CardContent>
      </Card>

      {/* Admin Panel Access Link */}
      <Card className="border-slate-100 shadow-sm rounded-2xl bg-slate-50/50">
        <CardHeader className="pb-3">
          <CardTitle className="text-base font-bold flex items-center gap-2 text-slate-800">
            <Shield className="w-4 h-4 text-emerald-600" />
            Area Administratif
          </CardTitle>
          <CardDescription className="text-xs">Khusus untuk pengelola sistem dan pengembang.</CardDescription>
        </CardHeader>
        <CardContent>
          <Link href="/admin" className="w-full">
            <Button className="w-full bg-slate-900 hover:bg-slate-800 text-white rounded-xl text-xs py-2.5 font-bold transition-all flex items-center justify-center gap-2">
              Buka Dashboard Admin
            </Button>
          </Link>
        </CardContent>
      </Card>

      {/* Info Card */}
      <Card className="border-slate-100 shadow-sm rounded-2xl bg-emerald-50/20 border-emerald-100/50">
        <CardContent className="p-4 flex gap-3 text-emerald-800 text-xs">
          <Info className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <div className="space-y-1">
            <span className="font-bold block">Tentang iMosque Safar</span>
            <p className="leading-relaxed text-[11px] text-emerald-700">
              Versi 2.0. Memadukan data spasial OpenStreetMap (OSM) dan komputasi rute Dijkstra/A* dengan machine learning untuk mencari masjid teroptimal di perjalanan Anda secara realtime.
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
