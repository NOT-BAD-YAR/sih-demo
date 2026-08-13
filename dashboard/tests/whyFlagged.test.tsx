import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { WhyFlaggedCard } from '../src/components/WhyFlaggedCard'
import type { RiskDrillDown } from '../src/types'

const drill: RiskDrillDown = {
  entity_ref: 'EMP045',
  current: { risk: 89, band: 'Critical' },
  history: [
    {
      ts: '2026-02-01T10:00:00Z',
      risk: 89,
      band: 'Critical',
      explanation: 'volume 120.00 spikes vs baseline mean 15.20',
    },
  ],
  explanation: 'volume 120.00 spikes vs baseline mean 15.20',
  baseline_snapshot: {
    volume: { count: 30, mean: 15.2, std: 4.1, min: 2, max: 40 },
    event_count: { count: 30, mean: 30, std: 5, min: 10, max: 90 },
  },
}

describe('WhyFlaggedCard', () => {
  it('renders the current risk, reason and baseline snapshot', () => {
    render(<WhyFlaggedCard drill={drill} />)
    expect(screen.getByText(/Why flagged\?/i)).toBeTruthy()
    expect(screen.getAllByText(/volume 120.00 spikes/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/Normal behavior \(baseline snapshot\)/i)).toBeTruthy()
    expect(screen.getByText(/volume ≈ 15.20 ± 4.10/)).toBeTruthy()
    expect(screen.getByText('EMP045')).toBeTruthy()
  })

  it('handles an empty baseline gracefully', () => {
    render(<WhyFlaggedCard drill={{ ...drill, baseline_snapshot: null }} />)
    expect(screen.getByText(/Why flagged\?/i)).toBeTruthy()
    expect(screen.queryByText(/Normal behavior/i)).toBeNull()
  })

  it('shows a loading state', () => {
    render(<WhyFlaggedCard drill={drill} loading />)
    expect(screen.getByText(/Loading/i)).toBeTruthy()
  })
})
