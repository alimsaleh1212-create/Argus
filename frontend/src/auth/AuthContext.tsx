import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'
import { clearToken } from '@/api/client'

interface AuthState {
  token: string | null
  role: string | null
  isAuthenticated: boolean
}

interface AuthContextValue extends AuthState {
  signIn: (token: string, role: string) => void
  signOut: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(() => {
    const token = sessionStorage.getItem('argus_token')
    const role = sessionStorage.getItem('argus_role')
    return { token, role, isAuthenticated: !!token }
  })

  const signIn = useCallback((token: string, role: string) => {
    sessionStorage.setItem('argus_token', token)
    sessionStorage.setItem('argus_role', role)
    setState({ token, role, isAuthenticated: true })
  }, [])

  const signOut = useCallback(() => {
    clearToken()
    sessionStorage.removeItem('argus_role')
    setState({ token: null, role: null, isAuthenticated: false })
  }, [])

  return (
    <AuthContext.Provider value={{ ...state, signIn, signOut }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
