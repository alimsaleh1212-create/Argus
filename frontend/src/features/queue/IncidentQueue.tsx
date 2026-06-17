import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table'
import { ChevronLeft, ChevronRight, Filter } from 'lucide-react'
import { useIncidentQueue, type IncidentSummary, type QueueFilters } from '@/api/incidents'
import { StatusBadge } from '@/components/StatusBadge'
import { SeverityBadge } from '@/components/SeverityBadge'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

const SEVERITY_OPTIONS = ['low', 'medium', 'high', 'critical']
const STATUS_OPTIONS = [
  'received', 'grounding', 'grounded',
  'triaging', 'enriching', 'responding',
  'awaiting_approval',
  'resolved', 'escalated', 'failed',
]
const PAGE_SIZE = 50

const col = createColumnHelper<IncidentSummary>()

const COLUMNS = [
  col.accessor('severity', {
    header: 'Severity',
    cell: (info) => <SeverityBadge severity={info.getValue()} />,
    size: 100,
  }),
  col.accessor('status', {
    header: 'Status',
    cell: (info) => <StatusBadge status={info.getValue()} />,
    size: 160,
  }),
  col.accessor('source', {
    header: 'Source',
    cell: (info) => (
      <span className="font-mono text-xs text-slate-400">{info.getValue()}</span>
    ),
    size: 80,
  }),
  col.accessor('summary', {
    header: 'Summary',
    cell: (info) => (
      <span className="truncate max-w-xs block text-slate-200 text-sm">
        {info.getValue() ?? <span className="text-slate-600 italic">—</span>}
      </span>
    ),
  }),
  col.accessor('disposition', {
    header: 'Disposition',
    cell: (info) => {
      const val = info.getValue()
      return val ? (
        <span className="text-xs text-slate-400 font-mono">{val}</span>
      ) : (
        <span className="text-slate-700">—</span>
      )
    },
    size: 140,
  }),
  col.accessor('updated_at', {
    header: 'Updated',
    cell: (info) => (
      <span className="text-xs text-slate-500 tabular-nums">
        {new Date(info.getValue()).toLocaleString()}
      </span>
    ),
    size: 150,
  }),
]

export function IncidentQueue() {
  const navigate = useNavigate()
  const [view, setView] = useState<'active' | 'resolved' | 'all'>('active')
  const [statusFilter, setStatusFilter] = useState<string[]>([])
  const [severityFilter, setSeverityFilter] = useState<string[]>([])
  const [page, setPage] = useState(0)
  const [showFilters, setShowFilters] = useState(false)

  const filters: QueueFilters = {
    view,
    status: statusFilter,
    severity: severityFilter,
    sort: '-updated_at',
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  }

  const { data, isLoading, isError, error } = useIncidentQueue(filters)

  const table = useReactTable({
    data: data?.items ?? [],
    columns: COLUMNS,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    rowCount: data?.total ?? 0,
  })

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0

  function toggleMulti(
    value: string,
    current: string[],
    setter: (v: string[]) => void
  ) {
    setter(
      current.includes(value)
        ? current.filter((x) => x !== value)
        : [...current, value]
    )
    setPage(0)
  }

  if (isError) {
    return (
      <ErrorState
        message={`Failed to load incidents: ${(error as Error)?.message ?? 'unknown error'}`}
      />
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-1 bg-[#0F172A] rounded-lg p-1">
          {(['active', 'resolved', 'all'] as const).map((v) => (
            <button
              key={v}
              onClick={() => { setView(v); setPage(0) }}
              className={cn(
                'px-3 py-1.5 text-sm rounded-md font-medium transition-colors cursor-pointer min-h-[36px]',
                view === v
                  ? 'bg-sky-400/20 text-sky-400'
                  : 'text-slate-400 hover:text-slate-200'
              )}
            >
              {v.charAt(0).toUpperCase() + v.slice(1)}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          {(statusFilter.length > 0 || severityFilter.length > 0) && (
            <button
              onClick={() => { setStatusFilter([]); setSeverityFilter([]); setPage(0) }}
              className="text-xs text-slate-400 hover:text-slate-200 underline cursor-pointer"
            >
              Clear filters
            </button>
          )}
          <button
            onClick={() => setShowFilters((f) => !f)}
            aria-label="Toggle filters"
            className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm cursor-pointer min-h-[36px] transition-colors',
              showFilters
                ? 'bg-sky-400/20 text-sky-400'
                : 'text-slate-400 hover:text-slate-200 bg-[#0F172A]'
            )}
          >
            <Filter className="w-4 h-4" aria-hidden="true" />
            Filters
            {(statusFilter.length + severityFilter.length) > 0 && (
              <span className="ml-1 bg-sky-400 text-slate-950 text-[10px] font-bold rounded-full w-4 h-4 flex items-center justify-center">
                {statusFilter.length + severityFilter.length}
              </span>
            )}
          </button>
        </div>
      </div>

      {/* Filter panel */}
      {showFilters && (
        <div className="bg-[#0F172A] border border-slate-800 rounded-lg p-4 grid grid-cols-2 gap-6">
          <div>
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Status</p>
            <div className="flex flex-wrap gap-1.5">
              {STATUS_OPTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => toggleMulti(s, statusFilter, setStatusFilter)}
                  className={cn(
                    'px-2 py-0.5 rounded text-xs cursor-pointer transition-colors min-h-[28px]',
                    statusFilter.includes(s)
                      ? 'bg-sky-400/20 text-sky-400 border border-sky-400/30'
                      : 'bg-slate-800 text-slate-400 hover:text-slate-200'
                  )}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
          <div>
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Severity</p>
            <div className="flex gap-1.5">
              {SEVERITY_OPTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => toggleMulti(s, severityFilter, setSeverityFilter)}
                  className={cn(
                    'px-2 py-0.5 rounded text-xs cursor-pointer transition-colors min-h-[28px]',
                    severityFilter.includes(s)
                      ? 'bg-sky-400/20 text-sky-400 border border-sky-400/30'
                      : 'bg-slate-800 text-slate-400 hover:text-slate-200'
                  )}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Table */}
      <div className="bg-[#0F172A] rounded-lg border border-slate-800 overflow-hidden">
        {isLoading ? (
          <div className="p-4 space-y-3">
            {[...Array(8)].map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : (data?.items.length ?? 0) === 0 ? (
          <EmptyState
            title="No incidents found"
            description={
              statusFilter.length || severityFilter.length
                ? 'Try clearing the filters above.'
                : view === 'active'
                ? 'No active incidents in the pipeline.'
                : 'No resolved incidents yet.'
            }
          />
        ) : (
          <table className="w-full text-sm" aria-label="Incident queue">
            <thead>
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id} className="border-b border-slate-800">
                  {hg.headers.map((header) => (
                    <th
                      key={header.id}
                      className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider"
                      style={{ width: header.getSize() }}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => navigate(`/incidents/${row.original.id}`)}
                  className="border-b border-slate-800/50 hover:bg-slate-800/40 cursor-pointer transition-colors"
                  aria-label={`Open incident ${row.original.id}`}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="px-4 py-3">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {data && data.total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-slate-400">
          <span>
            {data.total} incidents · page {page + 1} of {totalPages}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              aria-label="Previous page"
              className="p-1.5 rounded hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer transition-colors min-h-[36px] min-w-[36px] flex items-center justify-center"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              aria-label="Next page"
              className="p-1.5 rounded hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer transition-colors min-h-[36px] min-w-[36px] flex items-center justify-center"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
