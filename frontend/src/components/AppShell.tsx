import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { LayoutDashboard, BarChart3, LogOut, GitGraph, ShieldAlert } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuth } from '@/auth/AuthContext'
import { ConnectionIndicator } from './ConnectionIndicator'
import { queryClient } from '@/api/queryClient'
import { useConnectionState, useSSEStream } from '@/api/stream'
import logo from '@/assets/argus-logo.jpg'

const navItems = [
  { to: '/map', label: 'Pipeline Map', icon: GitGraph },
  { to: '/attention', label: 'Human Attention', icon: ShieldAlert },
  { to: '/queue', label: 'Queue', icon: LayoutDashboard },
  { to: '/kpis', label: 'KPIs', icon: BarChart3 },
]

export function AppShell() {
  const { signOut, token } = useAuth()
  const navigate = useNavigate()
  const connectionState = useConnectionState()
  useSSEStream(token ?? null)

  function handleSignOut() {
    signOut()
    queryClient.clear()
    navigate('/login', { replace: true })
  }

  return (
    <div className="flex h-screen bg-[#020617] overflow-hidden">
      {/* Sidebar */}
      <aside className="w-56 flex-shrink-0 bg-[#0F172A] border-r border-slate-800 flex flex-col">
        {/* Brand */}
        <button
          type="button"
          onClick={() => navigate('/map')}
          aria-label="Go to Pipeline Map"
          className="flex items-center gap-3 px-4 py-4 border-b border-slate-800 transition-colors hover:bg-slate-800/60 cursor-pointer text-left"
        >
          <img src={logo} alt="Argus" className="w-11 h-11 rounded-lg object-cover flex-shrink-0" />
          <span className="text-base font-bold text-slate-50 tracking-tight">Argus</span>
        </button>

        {/* Nav */}
        <nav className="flex-1 py-4 px-2 space-y-1" aria-label="Main navigation">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2.5 px-3 py-2.5 rounded-md text-sm font-medium transition-colors cursor-pointer',
                  isActive
                    ? 'bg-sky-400/10 text-sky-400 border border-sky-400/20'
                    : 'text-slate-400 hover:text-slate-50 hover:bg-slate-800'
                )
              }
            >
              <Icon className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* Footer: connection indicator + sign out */}
        <div className="px-3 py-4 border-t border-slate-800 space-y-3">
          <ConnectionIndicator state={connectionState} />
          <button
            onClick={handleSignOut}
            className="flex w-full items-center gap-2.5 px-3 py-2.5 rounded-md text-sm text-slate-400 hover:text-slate-50 hover:bg-slate-800 transition-colors cursor-pointer min-h-[44px]"
            aria-label="Sign out"
          >
            <LogOut className="w-4 h-4" aria-hidden="true" />
            Sign out
          </button>
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar */}
        <header className="h-14 border-b border-slate-800 bg-[#0F172A] flex items-center px-6 flex-shrink-0">
          <div id="topbar-title" className="text-sm text-slate-400 font-medium" />
        </header>

        {/* Scrollable content (full-bleed; pages own their padding) */}
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
