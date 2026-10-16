import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "@/lib/auth";

export const metadata: Metadata = { title: "Pulse Console", description: "Pulse notification console" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body><Providers>{children}</Providers></body>
    </html>
  );
}
