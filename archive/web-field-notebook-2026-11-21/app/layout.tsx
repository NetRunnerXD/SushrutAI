import type { Metadata, Viewport } from "next";
import {
  Courier_Prime, Martel, Noto_Sans, Noto_Sans_Bengali, Noto_Sans_Devanagari, Noto_Sans_Gujarati, Noto_Sans_Kannada,
  Noto_Sans_Malayalam, Noto_Sans_Mono, Noto_Sans_Oriya, Noto_Sans_Tamil, Noto_Sans_Telugu,
} from "next/font/google";
import "./globals.css";
import { Shell } from "@/components/Shell";
import { PREFS_BOOT } from "@/lib/prefs-boot";

// Self-hosted, subset fonts (no layout shift, work offline). Browsers download a script's file only when a
// character of that script is on screen (unicode-range), so Tamil users don't download Bengali.
const noto = Noto_Sans({ display: "swap", weight: ["400", "600", "700"], subsets: ["latin", "devanagari"], variable: "--font-noto" });
const deva = Noto_Sans_Devanagari({ display: "swap", weight: ["400", "600", "700"], subsets: ["devanagari"], variable: "--font-noto-deva" });
const beng = Noto_Sans_Bengali({ display: "swap", weight: ["400", "600", "700"], subsets: ["bengali"], variable: "--font-noto-beng" });
const taml = Noto_Sans_Tamil({ display: "swap", weight: ["400", "600", "700"], subsets: ["tamil"], variable: "--font-noto-taml" });
const telu = Noto_Sans_Telugu({ display: "swap", weight: ["400", "600", "700"], subsets: ["telugu"], variable: "--font-noto-telu" });
const gujr = Noto_Sans_Gujarati({ display: "swap", weight: ["400", "600", "700"], subsets: ["gujarati"], variable: "--font-noto-gujr" });
const knda = Noto_Sans_Kannada({ display: "swap", weight: ["400", "600", "700"], subsets: ["kannada"], variable: "--font-noto-knda" });
const mlym = Noto_Sans_Malayalam({ display: "swap", weight: ["400", "600", "700"], subsets: ["malayalam"], variable: "--font-noto-mlym" });
const orya = Noto_Sans_Oriya({ display: "swap", weight: ["400", "600", "700"], subsets: ["oriya"], variable: "--font-noto-orya" });
const mono = Noto_Sans_Mono({ display: "swap", weight: ["400", "600"], subsets: ["latin"], variable: "--font-noto-mono" });
// Field Notebook display + numerals: Martel (Latin + Devanagari serif headings), Courier Prime (typewriter numerals).
const martel = Martel({ display: "swap", weight: ["400", "700", "800"], subsets: ["latin", "devanagari"], variable: "--font-martel" });
const courier = Courier_Prime({ display: "swap", weight: ["400", "700"], subsets: ["latin"], variable: "--font-courier" });
const fontVars = [noto, deva, beng, taml, telu, gujr, knda, mlym, orya, mono, martel, courier].map((f) => f.variable).join(" ");

export const metadata: Metadata = {
  title: { default: "SushrutAI", template: "%s · SushrutAI" },
  description: "Health help in your language. Information only — not a substitute for a doctor.",
  manifest: "/manifest.webmanifest",
};

export const viewport: Viewport = {
  width: "device-width", initialScale: 1, viewportFit: "cover", interactiveWidget: "resizes-content",
  themeColor: [{ media: "(prefers-color-scheme: light)", color: "#F4EFE4" },
               { media: "(prefers-color-scheme: dark)", color: "#121926" }],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={fontVars} suppressHydrationWarning>
      <head>
        {/* apply saved text size / theme / contrast before first paint */}
        <script dangerouslySetInnerHTML={{ __html: PREFS_BOOT }} />
      </head>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
