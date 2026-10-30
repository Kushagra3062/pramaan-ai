import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Pramaan AI — Evidence-first intelligence",
  description: "Turn fragmented documents into evidence-backed decisions.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
