"use client";

import dynamic from "next/dynamic";
import React, { useState, useEffect } from "react";

const MapComponent = dynamic(() => import("./MapComponent"), {
  ssr: false,
  loading: () => (
    <div className="w-full h-full bg-slate-100 animate-pulse flex items-center justify-center">
      <span className="text-slate-400 font-medium">Memuat Peta...</span>
    </div>
  )
});

const MapViewer = React.memo(function MapViewer(props: any) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) return (
    <div className="w-full h-full bg-slate-100 flex items-center justify-center">
      <span className="text-slate-400 font-medium">Memuat Peta...</span>
    </div>
  );

  return <MapComponent {...props} />;
});

export default MapViewer;
