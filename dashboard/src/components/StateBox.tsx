interface Props {
  loading?: boolean
  empty?: boolean
  emptyText?: string
  error?: string | null
  retry?: () => void
}

export function StateBox({ loading, empty, emptyText, error, retry }: Props) {
  if (error) {
    return (
      <div className="state-box error" role="alert">
        <div>Error loading data</div>
        <div className="muted mt-10">{error}</div>
        {retry && (
          <button className="btn sm mt-10" onClick={retry}>
            Retry
          </button>
        )}
      </div>
    )
  }
  if (loading) {
    return (
      <div className="state-box">
        <span className="spinner" /> Loading…
      </div>
    )
  }
  if (empty) {
    return <div className="state-box">{emptyText ?? 'No data to show yet.'}</div>
  }
  return null
}
