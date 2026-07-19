const configuredApiBase = process.env.NEXT_PUBLIC_API_BASE?.trim().replace(/\/+$/, "");

export const API_BASE = configuredApiBase || (
  typeof window !== "undefined"
    ? `http://${window.location.hostname}:8000`
    : "http://127.0.0.1:8000"
);
