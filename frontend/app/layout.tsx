import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "BookDistill · 阅读工作台",
  description: "带来源引用的书籍阅读与问答工作台。",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">{children}</body>
    </html>
  );
}
