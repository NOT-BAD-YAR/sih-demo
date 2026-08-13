import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RiskBadge } from '../src/components/RiskBadge'
import { bandFor } from '../src/lib/bands'

describe('bandFor', () => {
  it('maps thresholds to bands', () => {
    expect(bandFor(10)).toBe('Low')
    expect(bandFor(25)).toBe('Medium')
    expect(bandFor(49)).toBe('Medium')
    expect(bandFor(50)).toBe('High')
    expect(bandFor(74)).toBe('High')
    expect(bandFor(75)).toBe('Critical')
    expect(bandFor(99)).toBe('Critical')
  })
})

describe('RiskBadge', () => {
  beforeEach(() => {
    // jsdom needs these to avoid CSS parsing noise; harmless.
    document.body.innerHTML = ''
  })

  it('shows value and band', () => {
    render(<RiskBadge risk={90} />)
    expect(screen.getByText('90 Critical')).toBeTruthy()
  })

  it('shows a dash when no risk is given', () => {
    render(<RiskBadge risk={null} />)
    expect(screen.getByText('— Low')).toBeTruthy()
  })

  it('can show the band only', () => {
    render(<RiskBadge risk={80} showValue={false} />)
    expect(screen.getByText('Critical')).toBeTruthy()
  })
})
