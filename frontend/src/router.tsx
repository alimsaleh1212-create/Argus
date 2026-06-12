import { createBrowserRouter, Navigate } from 'react-router-dom'
import { AuthProvider } from '@/auth/AuthContext'
import { RequireAuth } from '@/auth/RequireAuth'
import { LoginPage } from '@/auth/LoginPage'
import { AppShell } from '@/components/AppShell'
import { lazy, Suspense } from 'react'
import { Skeleton } from '@/components/ui/skeleton'

const IncidentQueue = lazy(() =>
  import('@/features/queue/IncidentQueue').then((m) => ({ default: m.IncidentQueue }))
)
const IncidentDetail = lazy(() =>
  import('@/features/incident/IncidentDetail').then((m) => ({ default: m.IncidentDetail }))
)
const TraceInspector = lazy(() =>
  import('@/features/trace/TraceInspector').then((m) => ({ default: m.TraceInspector }))
)
const KpiDashboard = lazy(() =>
  import('@/features/kpis/KpiDashboard').then((m) => ({ default: m.KpiDashboard }))
)

function Loading() {
  return (
    <div className="space-y-3 p-6">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-3/4" />
    </div>
  )
}

export const router = createBrowserRouter([
  {
    path: '/login',
    element: (
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    ),
  },
  {
    path: '/',
    element: (
      <AuthProvider>
        <RequireAuth>
          <AppShell />
        </RequireAuth>
      </AuthProvider>
    ),
    children: [
      { index: true, element: <Navigate to="/queue" replace /> },
      {
        path: 'queue',
        element: (
          <Suspense fallback={<Loading />}>
            <IncidentQueue />
          </Suspense>
        ),
      },
      {
        path: 'incidents/:id',
        element: (
          <Suspense fallback={<Loading />}>
            <IncidentDetail />
          </Suspense>
        ),
      },
      {
        path: 'incidents/:id/trace',
        element: (
          <Suspense fallback={<Loading />}>
            <TraceInspector />
          </Suspense>
        ),
      },
      {
        path: 'kpis',
        element: (
          <Suspense fallback={<Loading />}>
            <KpiDashboard />
          </Suspense>
        ),
      },
    ],
  },
])
