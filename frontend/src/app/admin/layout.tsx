import type { Metadata } from "next";
import Link from "next/link";
import { Home, LayoutDashboard } from "lucide-react";

export const metadata: Metadata = {
  title: "iMosque - Admin Dashboard",
  description: "Manajemen data dan performa komputasi iMosque",
};

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-screen w-screen bg-slate-50 text-slate-900 overflow-hidden font-sans">
      {/* Sidebar Admin (Hidden on Mobile, Visible on Desktop) */}
      <aside className="hidden md:flex w-64 bg-white border-r border-slate-200 flex-col shrink-0 shadow-sm">
        {/* Header Logo */}
        <div className="p-6 border-b border-slate-200 flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-emerald-500 to-teal-400 flex items-center justify-center text-slate-950 font-black shadow-lg shadow-emerald-500/20">
            iM
          </div>
          <div>
            <h1 className="text-base font-bold tracking-wide text-slate-900">iMosque Admin</h1>
            <span className="text-[10px] text-emerald-600 font-bold uppercase tracking-wider">Dashboard</span>
          </div>
        </div>

        {/* Navigation Menu */}
        <nav className="flex-1 px-4 py-6 space-y-1.5 overflow-y-auto">
          <div className="px-3 mb-2 text-[10px] font-bold text-slate-500 uppercase tracking-wider">
            Menu Utama
          </div>
          <Link
            href="/admin"
            className="flex items-center gap-3 px-3.5 py-3 text-sm font-semibold rounded-xl text-emerald-700 bg-emerald-50 border border-emerald-200 transition-all duration-300"
          >
            <LayoutDashboard className="w-4 h-4 text-emerald-600" />
            Dashboard & Data
          </Link>
        </nav>

        {/* Footer Sidebar */}
        <div className="p-4 border-t border-slate-200">
          <Link
            href="/"
            className="flex items-center justify-center gap-2 w-full py-2.5 px-4 text-xs font-semibold rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 transition-all duration-300 border border-slate-200"
          >
            <Home className="w-3.5 h-3.5" />
            Kembali ke Peta
          </Link>
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="flex-1 flex flex-col min-w-0 bg-slate-50 overflow-hidden relative">
        <header className="h-16 border-b border-slate-200 bg-white/95 flex items-center justify-between px-4 md:px-8 z-10 shrink-0 shadow-sm">
          {/* Mobile Logo & Title */}
          <div className="flex items-center gap-2.5 md:hidden">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-emerald-500 to-teal-400 flex items-center justify-center text-slate-950 text-xs font-black shadow-md">
              iM
            </div>
            <div className="flex flex-col">
              <span className="text-xs font-bold leading-tight text-slate-900">iMosque Admin</span>
              <span className="text-[8px] text-emerald-600 font-bold uppercase tracking-wider">Mobile Panel</span>
            </div>
          </div>

          {/* Desktop Greeting */}
          <div className="hidden md:block text-sm text-slate-600 font-medium">
            Selamat Datang di Portal Admin
          </div>

          {/* Actions & Connection Info */}
          <div className="flex items-center gap-3">
            <div className="hidden md:flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse"></span>
              <span className="text-xs text-slate-500 font-bold">API Server: Connected</span>
            </div>
            <Link
              href="/"
              className="md:hidden flex items-center gap-1.5 py-1.5 px-3.5 text-[10px] font-bold rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-200 transition-all duration-300"
            >
              <Home className="w-3.5 h-3.5" />
              Kembali
            </Link>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto p-4 md:p-8 custom-scrollbar">
          {children}
        </div>
      </main>
    </div>
  );
}
