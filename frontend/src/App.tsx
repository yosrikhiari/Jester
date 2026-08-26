/* Minimal JesterAppShell scaffold (design §3.2). Real shell + nav land later. */
import './App.css'

export default function App() {
  return (
    <div className="jester-root" style={{ minBlockSize: '100%', padding: 'var(--jester-s-8)' }}>
      <h1 style={{ fontFamily: 'Fraunces, serif', fontSize: 'var(--jester-fs-h1)', color: 'var(--jester-gold)' }}>
        Jester
      </h1>
      <p style={{ color: 'var(--jester-ink-mute)' }}>
        Frontend scaffold up. Tokens + base CSS are live; components port from
        <code> inspiration/design-system-revision/</code>.
      </p>
      <button
        className="jester-focusable"
        style={{
          border: '1px solid var(--jester-line)',
          background: 'var(--jester-surface)',
          color: 'var(--jester-ink)',
          borderRadius: 'var(--jester-r-ctrl)',
          padding: 'var(--jester-s-4) var(--jester-s-6)',
          cursor: 'pointer',
        }}
      >
        Primary action
      </button>
    </div>
  )
}
