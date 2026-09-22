import type { Metadata } from "next";
import Shell from "@/components/shell/Shell";
import AuthProvider from "@/components/AuthProvider";
import { ToastProvider } from "@/components/ui/Toast";
import "../styles/tokens.css";
import "../styles/base.css";
import "@/components/shell/shell.css";
import "@/components/ui/button.css";
import "@/components/ui/form.css";
import "@/components/ui/card.css";
import "@/components/ui/kpi.css";
import "@/components/ui/table.css";
import "@/components/charts/charts.css";
import "@/components/ui/overlays.css";
import "@/components/ui/misc.css";

export const metadata: Metadata = {
  title: "laya ops",
  description: "LayaWatch observability console for the laya decision engine",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        {/* Blocking external theme resolver (D-012): no inline scripts, resolves before paint. */}
        <script src="/theme-init.js" />
      </head>
      <body>
        <AuthProvider>
          <ToastProvider>
            <Shell>{children}</Shell>
          </ToastProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
