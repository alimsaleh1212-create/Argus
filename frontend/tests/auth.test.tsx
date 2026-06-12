import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from '@/auth/AuthContext'
import { RequireAuth } from '@/auth/RequireAuth'

// Helper to read location in tests
function LocationDisplay() {
  const loc = useLocation()
  return <div data-testid="path">{loc.pathname}</div>
}

describe('AuthProvider + RequireAuth', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('redirects unauthenticated user to /login', () => {
    render(
      <MemoryRouter initialEntries={['/queue']}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LocationDisplay />} />
            <Route
              path="/queue"
              element={
                <RequireAuth>
                  <div>Queue</div>
                </RequireAuth>
              }
            />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    )
    expect(screen.getByTestId('path').textContent).toBe('/login')
  })

  it('admits authenticated user past the guard', () => {
    sessionStorage.setItem('argus_token', 'test-token')
    sessionStorage.setItem('argus_role', 'admin')

    render(
      <MemoryRouter initialEntries={['/queue']}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<div>Login</div>} />
            <Route
              path="/queue"
              element={
                <RequireAuth>
                  <div data-testid="queue-content">Queue</div>
                </RequireAuth>
              }
            />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    )
    expect(screen.getByTestId('queue-content')).toBeInTheDocument()
  })

  it('stores token in sessionStorage on signIn', () => {
    function SignInButton() {
      const { signIn } = useAuth()
      return (
        <button onClick={() => signIn('my-jwt-token', 'admin')}>
          Sign in
        </button>
      )
    }

    render(
      <MemoryRouter>
        <AuthProvider>
          <SignInButton />
        </AuthProvider>
      </MemoryRouter>
    )

    act(() => {
      screen.getByRole('button', { name: /sign in/i }).click()
    })

    expect(sessionStorage.getItem('argus_token')).toBe('my-jwt-token')
  })

  it('clears token from sessionStorage on signOut', () => {
    sessionStorage.setItem('argus_token', 'existing-token')

    function SignOutButton() {
      const { signOut } = useAuth()
      return <button onClick={signOut}>Sign out</button>
    }

    render(
      <MemoryRouter>
        <AuthProvider>
          <SignOutButton />
        </AuthProvider>
      </MemoryRouter>
    )

    act(() => {
      screen.getByRole('button', { name: /sign out/i }).click()
    })

    expect(sessionStorage.getItem('argus_token')).toBeNull()
  })
})
