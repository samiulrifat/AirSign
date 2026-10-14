import { NavLink, Link } from 'react-router-dom'
import type { ReactNode } from 'react'

export function Logo() {
  return (
    <svg viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="16" fill="currentColor" opacity="0.08" />
      <path
        d="M12 40c6-14 10-20 14-20s4 18 9 18 6-22 11-22 5 10 6 14"
        fill="none"
        stroke="var(--accent)"
        strokeWidth="5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function TopBar({ links }: { links?: { to: string; label: string }[] }) {
  return (
    <header className="topbar">
      <Link to="/" className="brand">
        <Logo />
        AirSign
      </Link>
      {links && (
        <nav>
          {links.map((l) => (
            <NavLink key={l.to} to={l.to} end>
              {l.label}
            </NavLink>
          ))}
        </nav>
      )}
    </header>
  )
}

export function SimBadge({ children = 'Simulated' }: { children?: ReactNode }) {
  return <span className="badge sim">{children}</span>
}

export function TierBadge({ tier }: { tier: number }) {
  const cls = tier === 1 ? 'ok' : tier === 2 ? 'warn' : 'bad'
  const text = tier === 1 ? 'Tier 1 · routine' : tier === 2 ? 'Tier 2 · stricter' : 'Tier 3 · two signatures'
  return <span className={`badge ${cls}`}>{text}</span>
}

export function Spinner() {
  return <span className="spinner" aria-label="Loading" />
}

export function CheckIcon({ bad = false }: { bad?: boolean }) {
  return (
    <div className={`check-anim${bad ? ' bad' : ''}`}>
      <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {bad ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M5 12.5l4.5 4.5L19 7.5" />}
      </svg>
    </div>
  )
}
