import { createContext, useContext, useState, useCallback } from 'react'

const AuthContext = createContext(null)

function parseRole(token) {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    return payload.role ?? null
  } catch {
    return null
  }
}

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem('fm_token'))
  const [role, setRole] = useState(() => {
    const t = localStorage.getItem('fm_token')
    return t ? parseRole(t) : null
  })
  const [username, setUsername] = useState(() => {
    const t = localStorage.getItem('fm_token')
    try {
      return t ? JSON.parse(atob(t.split('.')[1])).sub : null
    } catch { return null }
  })

  const login = useCallback(async (user, password) => {
    const res = await fetch('/api/auth/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ username: user, password }),
    })
    if (!res.ok) throw new Error('Invalid credentials')
    const data = await res.json()
    localStorage.setItem('fm_token', data.access_token)
    setToken(data.access_token)
    setRole(data.role)
    setUsername(user)
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem('fm_token')
    setToken(null)
    setRole(null)
    setUsername(null)
  }, [])

  return (
    <AuthContext.Provider value={{ token, role, username, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
