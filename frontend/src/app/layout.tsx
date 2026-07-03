import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/sonner";
import { ThemeProvider } from "@/components/theme-provider";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "iMosque - Modern Next.js",
  description: "iMosque OSM Dijkstra ML WebApp Realtime",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="id" suppressHydrationWarning>
      <head>
        <link rel="manifest" href="/manifest.json" />
        <meta name="theme-color" content="#0f172a" />
        <script
          dangerouslySetInnerHTML={{
            __html: `
              // === PWA Service Worker Registration ===
              if ('serviceWorker' in navigator) {
                if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
                  // Di lingkungan development, unregister SW aktif agar tidak mengacaukan caching Next.js dev bundles/hot-reloading
                  navigator.serviceWorker.getRegistrations().then(function(registrations) {
                    for (let registration of registrations) {
                      registration.unregister().then(function(success) {
                        if (success) {
                          console.log('[SW] Berhasil melakukan unregister SW aktif di development.');
                          // Gunakan sessionStorage dan setTimeout untuk mencegah gangguan hidrasi React & reload loop
                          if (!sessionStorage.getItem('sw_unregistered_reload')) {
                            sessionStorage.setItem('sw_unregistered_reload', 'true');
                            setTimeout(function() {
                              window.location.reload();
                            }, 500);
                          }
                        }
                      });
                    }
                  });
                } else {
                  window.addEventListener('load', function() {
                    navigator.serviceWorker.register('/sw.js').then(function(reg) {
                      console.log('[SW] Service Worker registered, scope:', reg.scope);
                    }).catch(function(err) {
                      console.warn('[SW] Service Worker registration failed:', err);
                    });
                  });
                }
              }

              // Hapus atribut bis_skin_checked jika sudah ada di DOM
              document.querySelectorAll('[bis_skin_checked]').forEach(el => el.removeAttribute('bis_skin_checked'));
              
              // Observasi dan hapus jika disisipkan kemudian
              const observer = new MutationObserver((mutations) => {
                mutations.forEach((mutation) => {
                  if (mutation.type === 'attributes' && mutation.attributeName === 'bis_skin_checked') {
                    mutation.target.removeAttribute('bis_skin_checked');
                  }
                });
              });
              observer.observe(document.documentElement, { 
                attributes: true, 
                subtree: true, 
                attributeFilter: ['bis_skin_checked'] 
              });
            `
          }}
        />
      </head>
      <body className={inter.className} suppressHydrationWarning>
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          {children}
          <Toaster position="top-center" richColors closeButton theme="system" />
        </ThemeProvider>
      </body>
    </html>
  );
}

