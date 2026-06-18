import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EvidencePanel } from '@/features/map/EvidencePanel'

describe('EvidencePanel', () => {
  it('renders the empty state when evidence is null', () => {
    render(<EvidencePanel evidence={null} />)
    expect(screen.getByText(/no evidence recorded/i)).toBeInTheDocument()
  })

  it('renders summary, verdict, and flags', () => {
    render(
      <EvidencePanel
        evidence={{
          summary: 'SSH brute force from Tor exit node',
          verdict: 'rule_match',
          flags: ['severity_defaulted', 'prior_failure'],
        }}
      />
    )
    expect(screen.getByText('SSH brute force from Tor exit node')).toBeInTheDocument()
    expect(screen.getByText('rule_match')).toBeInTheDocument()
    expect(screen.getByText('severity_defaulted')).toBeInTheDocument()
    expect(screen.getByText('prior_failure')).toBeInTheDocument()
  })

  it('renders triage, enrichment, and response sections with scores', () => {
    render(
      <EvidencePanel
        evidence={{
          summary: 'C2 beacon',
          triage: {
            verdict: 'real',
            confidence: 0.82,
            assessed_severity: 'high',
            rationale: 'Beaconing pattern to known C2',
            cited_evidence: ['beacon interval 300s'],
          },
          enrichment: {
            assessment: 'confirmed',
            confidence: 0.71,
            correlation_summary: 'IP matches threat intel and corpus T1071',
            external_findings: ['intel: 45.142.212.100 malicious'],
            internal_findings: ['prior_incident: C2 beacon last week'],
            cited_evidence: ['45.142.212.100'],
          },
          response: {
            plan: {
              playbook_id: 'isolate_host',
              selected_by: 'deterministic',
              rationale: 'Matched C2 containment playbook',
            },
            verification: { verdict: 'verified' },
            results: [{ action_id: 'isolate_host', status: 'applied' }],
          },
        }}
      />
    )
    // Triage section
    expect(screen.getByText(/Triage · real · 82%/i)).toBeInTheDocument()
    expect(screen.getByText('Beaconing pattern to known C2')).toBeInTheDocument()
    // Enrichment section
    expect(screen.getByText(/Enrichment · confirmed · 71%/i)).toBeInTheDocument()
    expect(screen.getByText('IP matches threat intel and corpus T1071')).toBeInTheDocument()
    expect(screen.getByText('intel: 45.142.212.100 malicious')).toBeInTheDocument()
    // Response section
    expect(screen.getByText('isolate_host')).toBeInTheDocument()
    expect(screen.getByText('via deterministic')).toBeInTheDocument()
    expect(screen.getByText(/verification/i)).toBeInTheDocument()
  })

  it('omits triage/enrichment/response sections when absent', () => {
    render(<EvidencePanel evidence={{ summary: 'noise', verdict: 'benign' }} />)
    expect(screen.queryByText(/triage/i)).toBeNull()
    expect(screen.queryByText(/enrichment/i)).toBeNull()
    expect(screen.queryByText(/response/i)).toBeNull()
  })
})
