/**
 * Settings Sync Utility
 * Auto-sync user settings ke database ArangoDB
 */
import { API_BASE } from "@/lib/config";

export type SearchSettingsPayload = {
  algorithm: string;
  profile: string;
  departureMode: "now" | "scheduled";
  currentTime: string;
  prayer: string;
  maxCandidates: string;
  bufferKm: string;
  autoBuild: boolean;
  fuelPricePerLiter: string;
  fuelEfficiencyKmPerLiter: string;
  operatingCostPerKm: string;
  tollCostPerKm: string;
};

export type PrayerSettingsPayload = {
  schedule: Array<{ name: string; time: string; isAlarmActive: boolean }>;
  hijriDate: string | null;
  masehiDate: string | null;
};

export type SettingsPayload = {
  searchSettings?: SearchSettingsPayload;
  prayerSettings?: PrayerSettingsPayload;
};

export type UserSettingsDocument = {
  search_settings?: Partial<SearchSettingsPayload>;
  prayer_settings?: Partial<PrayerSettingsPayload>;
  updated_at?: string;
};

let saveTimeout: ReturnType<typeof setTimeout> | null = null;

/**
 * Generate unique device/user ID
 * Menggunakan fingerprint browser untuk identifikasi unik
 */
export function getUserId(): string {
  if (typeof window === "undefined") return "server";
  
  // Cek localStorage untuk existing ID
  let userId = localStorage.getItem("imosque_user_id");
  
  if (!userId) {
    // Generate ID baru dari browser fingerprint
    const fingerprint = [
      navigator.userAgent,
      navigator.language,
      screen.width + "x" + screen.height,
      new Date().getTimezoneOffset(),
      !!window.sessionStorage,
      !!window.localStorage
    ].join("_");
    
    // Hash sederhana
    let hash = 0;
    for (let i = 0; i < fingerprint.length; i++) {
      const char = fingerprint.charCodeAt(i);
      hash = ((hash << 5) - hash) + char;
      hash = hash & hash; // Convert to 32bit integer
    }
    
    userId = `device_${Math.abs(hash).toString(36)}_${Date.now().toString(36)}`;
    localStorage.setItem("imosque_user_id", userId);
  }
  
  return userId;
}

/**
 * Save settings to database
 */
export async function saveSettingsToDatabase(settings: SettingsPayload): Promise<boolean> {
  try {
    if (saveTimeout) {
      clearTimeout(saveTimeout);
      saveTimeout = null;
    }
    const userId = getUserId();
    const updatedAt = new Date().toISOString();
    
    const response = await fetch(`${API_BASE}/api/v1/user-settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: userId,
        search_settings: settings.searchSettings,
        prayer_settings: settings.prayerSettings,
        updated_at: updatedAt
      })
    });
    
    if (!response.ok) {
      console.error("Failed to save settings to database:", await response.text());
      return false;
    }
    
    const result = await response.json();
    console.log("Settings saved to database:", result);
    return true;
  } catch (error) {
    console.error("Error saving settings to database:", error);
    return false;
  }
}

/**
 * Load settings from database
 */
export async function loadSettingsFromDatabase(): Promise<UserSettingsDocument | null> {
  try {
    const userId = getUserId();
    
    const response = await fetch(`${API_BASE}/api/v1/user-settings/${userId}`);
    
    if (!response.ok) {
      if (response.status === 404) {
        console.log("No settings found in database for user:", userId);
        return null;
      }
      throw new Error(`Failed to load settings: ${response.statusText}`);
    }
    
    const result = await response.json();
    
    if (result.status === "success" && result.data) {
      console.log("Settings loaded from database:", result.data);
      return result.data as UserSettingsDocument;
    }
    
    return null;
  } catch (error) {
    console.error("Error loading settings from database:", error);
    throw error;
  }
}

/**
 * Delete settings from database
 */
export async function deleteSettingsFromDatabase(): Promise<boolean> {
  try {
    const userId = getUserId();
    
    const response = await fetch(`${API_BASE}/api/v1/user-settings/${userId}`, {
      method: "DELETE"
    });
    
    if (!response.ok) {
      throw new Error(`Failed to delete settings: ${response.statusText}`);
    }
    
    console.log("Settings deleted from database");
    return true;
  } catch (error) {
    console.error("Error deleting settings from database:", error);
    return false;
  }
}

/**
 * Debounced save untuk menghindari terlalu banyak request
 */
export function debouncedSaveSettings(settings: SettingsPayload, delay: number = 2000) {
  if (saveTimeout) {
    clearTimeout(saveTimeout);
  }
  
  saveTimeout = setTimeout(() => {
    saveTimeout = null;
    saveSettingsToDatabase(settings);
  }, delay);
}
