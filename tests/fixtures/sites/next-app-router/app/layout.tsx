import type { Metadata } from "next";

export const metadata: Metadata = {
  title: { default: "Acme", template: "%s | Acme" },
  description: "Acme builds tools for teams.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header>
          <nav>
            <a href="/">Home</a>
            <a href="/pricing">Pricing</a>
          </nav>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
