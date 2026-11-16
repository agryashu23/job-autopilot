import React, { useEffect, useState } from 'react'

const TABS = [
  ['queued', 'Review'], ['approved', 'Approved'], ['needs_attention', 'Needs attention'],
  ['new', 'Near misses'], ['expired', 'Expired'], ['skipped', 'Skipped'], ['applied', 'Applied'],
]
const DECIDABLE = new Set(['queued', 'new', 'needs_attention', 'expired'])

function ago(ts) {
  if (!ts) return ''
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}

const esc = s => String(s).replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const linkify = s => esc(s).replace(/(https?:\/\/[^\s<]+)/g,
  '<a href="$1" target="_blank" rel="noreferrer">$1</a>')

function RunPanel({ onFinished }) {
  const [data, setData] = useState({ running: false, runs: [], latest_log: '' })
  const [showLog, setShowLog] = useState(false)
  const [starting, setStarting] = useState(false)

  async function refresh() {
    try { setData(await fetch('/api/runs').then(r => r.json())) } catch { /* server away */ }
  }
  useEffect(() => { refresh() }, [])
  useEffect(() => {
    if (!data.running) return
    const iv = setInterval(async () => {
      const d = await fetch('/api/runs').then(r => r.json()).catch(() => null)
      if (!d) return
      setData(d)
      if (!d.running) { clearInterval(iv); onFinished() }
    }, 4000)
    return () => clearInterval(iv)
  }, [data.running])

  async function start() {
    setStarting(true)
    await fetch('/api/jobs/discover', { method: 'POST' })
    await refresh()
    setStarting(false)
  }

  const last = data.runs[0]
  return (
    <div className="run-panel">
      <div className="run-row">
        <button className="find" disabled={data.running || starting} onClick={start}>
          {data.running ? '⏳ Searching boards…' : '🔄 Find new jobs'}
        </button>
        {last ? (
          <span className={`run-summary ${last.ok === 0 ? 'bad' : ''}`}>
            Last run {ago(last.started_at)}{last.finished_at ? '' : ' (in progress)'}:{' '}
            {last.summary || (last.finished_at ? 'no summary' : '…')}
          </span>
        ) : <span className="run-summary">No runs yet. Click Find new jobs.</span>}
        <button className="link" onClick={() => setShowLog(s => !s)}>
          {showLog ? 'hide log' : 'show log'}
        </button>
      </div>
      {showLog && <pre className="run-log">{data.latest_log || '(empty)'}</pre>}
    </div>
  )
}

function Marketplaces() {
  const [items, setItems] = useState([])
  useEffect(() => {
    fetch('/api/marketplaces').then(r => r.json()).then(d => setItems(d.marketplaces || []))
  }, [])
  return (
    <div className="market">
      <p className="market-hint">
        These platforms do not post scrapeable listings. Sign up once (a vetting test or an
        AI interview) and they match you to hourly contracts on an ongoing basis.
      </p>
      {items.map(m => (
        <div className="market-card" key={m.name}>
          <a href={m.url} target="_blank" rel="noreferrer"><b>{m.name}</b> ↗</a>
          <span>{m.blurb}</span>
        </div>
      ))}
    </div>
  )
}

export default function JobsBoard() {
  const [tab, setTab] = useState('queued')
  const [jobs, setJobs] = useState([])
  const [counts, setCounts] = useState({})
  const [kit, setKit] = useState(null)   // {job, kit}

  function load(t = tab) {
    if (t === 'marketplaces') return
    fetch(`/api/jobs?status=${t}`).then(r => r.json()).then(d => {
      setJobs(d.jobs); setCounts(d.counts)
    })
  }
  useEffect(() => { load(tab) }, [tab])

  async function openKit(job, openPage) {
    const k = await fetch(`/api/jobs/${job.id}/apply-kit`).then(r => r.json())
    setKit({ job, kit: k })
    if (openPage) window.open(job.apply_url || job.url, '_blank', 'noopener')
  }

  async function decide(job, decision) {
    await fetch(`/api/jobs/${job.id}/decide`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    })
    if (decision === 'approved') await openKit(job, true)
    load()
  }

  return (
    <main className="jobs">
      <RunPanel onFinished={() => load()} />

      <div className="tabs">
        {TABS.map(([k, label]) => (
          <button key={k} className={tab === k ? 'active' : ''} onClick={() => setTab(k)}>
            {label} {counts[k] ? `(${counts[k]})` : ''}
          </button>
        ))}
        <button className={`market-tab ${tab === 'marketplaces' ? 'active' : ''}`}
                onClick={() => setTab('marketplaces')}>★ Marketplaces</button>
      </div>

      {tab === 'marketplaces' ? <Marketplaces /> : (
        <>
          {jobs.length === 0 && (
            <p className="empty">Nothing here yet. Use <b>Find new jobs</b> above.</p>
          )}
          <div className="job-list">
            {jobs.map(j => (
              <div className="job-card" key={j.id}>
                <div className="job-main">
                  <div className="job-title">
                    <a href={j.url} target="_blank" rel="noreferrer">{j.title}</a>
                    {j.match_score != null && (
                      <span className={`badge fit ${j.match_score >= 60 ? 'hi' : j.match_score >= 45 ? 'mid' : 'lo'}`}>
                        {Math.round(j.match_score)}% match
                      </span>
                    )}
                    {!!j.is_easy_apply && <span className="badge ok">EASY APPLY</span>}
                    {j.apply_url && <span className="badge ext">EXTERNAL</span>}
                    {j.rate && <span className="badge pay">💰 {j.rate}</span>}
                  </div>
                  <div className="job-meta">
                    {j.company} · {j.location} · <span className="src">{j.source}</span>
                  </div>
                  {j.apply_url && (
                    <div className="apply-link">Apply at:{' '}
                      <a href={j.apply_url} target="_blank" rel="noreferrer">
                        {j.apply_url.replace(/^https?:\/\//, '').slice(0, 90)}
                      </a>
                    </div>
                  )}
                  {j.fit_reason && <div className="fit-reason">🤖 {j.fit_reason}</div>}
                  {(j.must_have || j.missing) && (
                    <div className="req-row">
                      {j.must_have && <span className="req">Requires: {j.must_have}</span>}
                      {j.missing && <span className="req miss">You lack: {j.missing}</span>}
                    </div>
                  )}
                  {j.note && !j.apply_url && (
                    <div className="job-note" dangerouslySetInnerHTML={{ __html: linkify(j.note) }} />
                  )}
                  <p className="job-desc">{(j.description || '').slice(0, 220)}…</p>
                </div>
                {DECIDABLE.has(tab) && (
                  <div className="job-actions">
                    <button className="approve" onClick={() => decide(j, 'approved')}>✓ Approve</button>
                    <button className="deny" onClick={() => decide(j, 'skipped')}>✗ Deny</button>
                  </div>
                )}
                {tab === 'approved' && (
                  <div className="job-actions">
                    <button className="approve" title="Record that you submitted this application"
                            onClick={() => decide(j, 'applied')}>✓ I applied</button>
                    <button className="kit-btn" onClick={() => openKit(j, true)}>📋 Kit</button>
                    <button className="deny" onClick={() => decide(j, 'skipped')}>✗ Drop</button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {kit && (
        <div className="kit-overlay" onClick={() => setKit(null)}>
          <div className="kit" onClick={e => e.stopPropagation()}>
            <h3>Apply kit — {kit.job.title}</h3>
            <p className="kit-hint">
              {kit.job.apply_url ? 'The company’s own application page' : 'The listing'} opened
              in a new tab. Paste from here; you press submit.
            </p>
            <label>Cover note</label>
            <textarea readOnly rows={5} value={kit.kit.cover_note}
                      onFocus={e => { e.target.select(); navigator.clipboard?.writeText(kit.kit.cover_note) }} />
            <label>Resume file</label>
            <code className="path">{kit.kit.resume_path}</code>
            <label>Common answers (click to copy)</label>
            <div className="answer-list">
              {kit.kit.answers.map((a, i) => (
                <div key={i} className="answer" title="click to copy"
                     onClick={() => navigator.clipboard?.writeText(String(a.value))}>
                  <span className="q">{a.questions[0]}</span>
                  <span className="v">{String(a.value)}</span>
                </div>
              ))}
            </div>
            <button className="cta" onClick={() => setKit(null)}>Done</button>
          </div>
        </div>
      )}
    </main>
  )
}
