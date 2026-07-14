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
  const isDevelopment = process.env.NODE_ENV !== "production";
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
                window.addEventListener('load', function() {
                  navigator.serviceWorker.register('/sw.js${isDevelopment ? "?dev=1" : ""}', { updateViaCache: 'all' }).then(function(reg) {
                    console.log('[SW] Service Worker registered, scope:', reg.scope);
                  }).catch(function(err) {
                    console.warn('[SW] Service Worker registration failed:', err);
                  });
                });
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
          defaultTheme="light"
          enableSystem={false}
          disableTransitionOnChange
          forcedTheme="light"
        >
          {children}
          <Toaster position="top-center" richColors closeButton theme="light" />
        </ThemeProvider>
      </body>
    </html>
  );
}
