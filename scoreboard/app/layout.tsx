import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "ledgerline | trust scoreboard for AI data agents",
  description:
    "Every agent claim is settled against reality. Brier scores, calibration, and skill-vs-luck verdicts for the agents working your DataHub catalog.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <header className="topbar">
            <Link href="/" className="brand">
              <b>ledgerline</b> / trust scoreboard
            </Link>
            <nav className="topnav">
              <Link href="/">Agents</Link>
              <Link href="/methodology">Methodology</Link>
              <a
                href="https://github.com/cnpierrepapi/ledgerline-ai"
                target="_blank"
                rel="noreferrer"
              >
                GitHub
              </a>
            </nav>
          </header>
          {children}
          <footer className="footer">
            <span>
              ledgerline: a trust ledger for AI data agents, built on DataHub.
            </span>
            <span>
              Apache 2.0 |{" "}
              <a
                href="https://github.com/cnpierrepapi/ledgerline-ai"
                target="_blank"
                rel="noreferrer"
              >
                cnpierrepapi/ledgerline-ai
              </a>
            </span>
          </footer>
        </div>
      </body>
    </html>
  );
}
