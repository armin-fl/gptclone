import type { Metadata } from "next";

import { AppQueryProvider } from "@/components/query-provider";

import "katex/dist/katex.min.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "GPTClone",
  description: "Local ChatGPT-style clone powered by Django, Next.js, and vLLM",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full font-sans text-slate-900">
        <AppQueryProvider>{children}</AppQueryProvider>
      </body>
    </html>
  );
}
