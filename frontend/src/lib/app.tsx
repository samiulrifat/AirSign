import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { get, type Config, type Me } from './api'

type AppState = {
  config: Config | null
  me: Me | null
  refreshMe: () => Promise<Me>
  setMe: (me: Me) => void
}

const Ctx = createContext<AppState | null>(null)

export function AppProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<Config | null>(null)
  const [me, setMe] = useState<Me | null>(null)

  const refreshMe = useCallback(async () => {
    const m = await get<Me>('/me')
    setMe(m)
    return m
  }, [])

  useEffect(() => {
    get<Config>('/config').then(setConfig).catch(() => setConfig(null))
    refreshMe().catch(() => setMe({ user: null }))
  }, [refreshMe])

  return <Ctx.Provider value={{ config, me, refreshMe, setMe }}>{children}</Ctx.Provider>
}

export function useApp(): AppState {
  const v = useContext(Ctx)
  if (!v) throw new Error('useApp outside AppProvider')
  return v
}

export function money(cents: number, currency = 'USD'): string {
  return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(cents / 100)
}

export function floorLabel(floor: number): string {
  const s = ['th', 'st', 'nd', 'rd']
  const v = floor % 100
  return `${floor}${s[(v - 20) % 10] || s[v] || s[0]} floor`
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : 'Something went wrong.'
}
