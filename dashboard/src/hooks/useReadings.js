import { useState, useEffect, useRef } from 'react'
import { useAuth } from '../contexts/AuthContext'

const CAP = 2880 // 48h at 30s intervals

export function useReadings() {
  const { token } = useAuth()
  const [readings, setReadings] = useState([])
  const latestTsRef = useRef(null)

  useEffect(() => {
    if (!token) return

    const headers = { Authorization: `Bearer ${token}` }

    async function seed() {
      try {
        const res = await fetch('/api/readings?limit=2880', { headers })
        if (!res.ok) return
        const rows = await res.json() // newest-first from API
        const ordered = [...rows].reverse() // oldest-first for chart
        setReadings(ordered)
        if (rows.length > 0) latestTsRef.current = rows[0].received_at
      } catch { /* network hiccup — retry on next poll */ }
    }

    async function poll() {
      try {
        const since = latestTsRef.current
        const url = since
          ? `/api/readings?since=${since}&limit=${CAP}`
          : '/api/readings?limit=2880'
        const res = await fetch(url, { headers })
        if (!res.ok) return
        const rows = await res.json() // since-query returns oldest-first
        if (rows.length === 0) return
        setReadings(prev => {
          const merged = [...prev, ...rows]
          return merged.slice(-CAP)
        })
        latestTsRef.current = rows[rows.length - 1].received_at
      } catch { /* ignore — will retry */ }
    }

    seed()
    const id = setInterval(poll, 15_000)
    return () => clearInterval(id)
  }, [token])

  return readings
}

export function useSummary() {
  const { token } = useAuth()
  const [summary, setSummary] = useState({ activity: 'finished', bubble_rate: 0, duration_s: 0, anomaly_active: false, recon_error: null })

  useEffect(() => {
    if (!token) return
    const headers = { Authorization: `Bearer ${token}` }

    async function fetch_() {
      try {
        const res = await fetch('/api/summary', { headers })
        if (res.ok) setSummary(await res.json())
      } catch { /* ignore */ }
    }

    fetch_()
    const id = setInterval(fetch_, 15_000)
    return () => clearInterval(id)
  }, [token])

  return summary
}

export function useBatches() {
  const { token } = useAuth()
  const [batches, setBatches] = useState([])

  useEffect(() => {
    if (!token) return
    fetch('/api/batches', { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : [])
      .then(setBatches)
      .catch(() => {})
  }, [token])

  return [batches, setBatches]
}
