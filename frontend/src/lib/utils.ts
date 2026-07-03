import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatDistance(km: number | string | undefined | null): string {
  if (km === undefined || km === null || km === "") return "N/A";
  const num = typeof km === "string" ? parseFloat(km) : km;
  if (isNaN(num)) return "N/A";
  
  if (num < 1.0) {
    const meters = Math.round(num * 1000);
    return `${meters} m`;
  }
  
  return num.toLocaleString("id-ID", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 2,
  }) + " km";
}
