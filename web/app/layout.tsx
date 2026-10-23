import type { Metadata } from "next";
import { Caprasimo, Figtree } from "next/font/google";
import "./globals.css";

const figtree = Figtree({ variable: "--font-figtree", subsets: ["latin"] });
const caprasimo = Caprasimo({ variable: "--font-caprasimo", subsets: ["latin"], weight: "400" });

export const metadata: Metadata = {
  title: "Connoisseur Companion",
  description:
    "Restaurants anywhere in the world and recipes to cook at home, recommended by a multi-agent assistant with sources.",
};

// Apply the saved light/dark choice before the first paint (no flash). Without one, the system setting wins.
const THEME_SCRIPT = `try{var t=localStorage.getItem("connoisseur:theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" suppressHydrationWarning className={`${figtree.variable} ${caprasimo.variable} h-full antialiased`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="flex h-full flex-col">{children}</body>
    </html>
  );
}
