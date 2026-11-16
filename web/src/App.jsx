import React, { useEffect, useState } from 'react'
import ProfileEditor from './ProfileEditor.jsx'
import JobsBoard from './JobsBoard.jsx'

export default function App() {
  const [view, setView] = useState('landing')          // landing | profile | jobs
  const [profile, setProfile] = useState(null)
  const [status, setStatus] = useState({})
  const [parsing, setParsing] = useState(false)
  const [parseError, setParseError] = useState('')

  useEffect(() => {
    fetch('/api/status').then(r => r.json()).then(setStatus).catch(() => {})
    fetch('/api/profile').then(r => r.json()).then(setProfile).catch(() => {})
  }, [])

  const [refining, setRefining] = useState(false)

  async function onResumeUpload(file) {
    setParsing(true)
    setParseError('')
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await fetch('/api/resume/parse', { method: 'POST', body: fd })
      if (!r.ok) throw new Error((await r.json()).detail || 'parse failed')
      const p = await r.json()
      setProfile(p)
      setView('profile')
      if (p.refine_id) pollRefine(p.refine_id)
    } catch (e) {
      setParseError(String(e.message || e))
    } finally {
      setParsing(false)
    }
  }

  function pollRefine(rid) {
    setRefining(true)
    const iv = setInterval(async () => {
      try {
        const job = await fetch(`/api/resume/refine/${rid}`).then(r => r.json())
        if (job.status === 'done') {
          clearInterval(iv)
          setRefining(false)
          // take the AI's cleaner fields, keep whatever preferences you set meanwhile
          setProfile(prev => ({ ...job.profile, preferences: prev?.preferences || job.profile.preferences }))
        }
      } catch { clearInterval(iv); setRefining(false) }
    }, 5000)
  }

  const engineBadge = status.model_ready
    ? <span className="badge ok">AI parser ready ({status.model})</span>
    : status.ollama_running
      ? <span className="badge warn">Ollama running — model downloading…</span>
      : <span className="badge warn">Heuristic parser (Ollama offline)</span>

  return (
    <div className="shell">
      <header>
        <div className="logo" onClick={() => setView('landing')}>⚡ Job Autopilot</div>
        <nav>
          <button className={view === 'profile' ? 'active' : ''} onClick={() => setView('profile')}>My Profile</button>
          <button className={view === 'jobs' ? 'active' : ''} onClick={() => setView('jobs')}>Jobs</button>
        </nav>
      </header>

      {view === 'landing' && (
        <main className="landing">
          <h1>Your next contract,<br />found while you sleep.</h1>
          <p className="sub">
            Upload your resume once. We build your job profile, hunt remote
            contract &amp; freelance work across job boards, and hand you an
            approve / deny list — you stay in control of every application.
          </p>
          <div className="cta-row">
            <label className="cta">
              {parsing ? 'Parsing resume…' : '📄 Upload resume & find jobs'}
              <input type="file" accept=".pdf" hidden disabled={parsing}
                     onChange={e => e.target.files[0] && onResumeUpload(e.target.files[0])} />
            </label>
            {profile?.name && (
              <button className="cta ghost" onClick={() => setView('jobs')}>
                Continue as {profile.name.split(' ')[0]} →
              </button>
            )}
          </div>
          {parseError && <p className="error">{parseError}</p>}
          <div className="badges">{engineBadge}</div>
          <div className="features">
            <div><b>1 · Parse</b><span>Local AI reads your resume — skills, projects, education. No cloud tokens, ever.</span></div>
            <div><b>2 · Prefer</b><span>Remote-only? Contract-only? Rate floor? Your filters are hard rules, not suggestions.</span></div>
            <div><b>3 · Approve</b><span>Ranked matches with an apply kit — every answer ready. You click submit, always.</span></div>
          </div>
        </main>
      )}

      {view === 'profile' && profile && (
        <>
          {refining && (
            <div className="refine-banner">
              🧠 Local AI is re-reading your resume to clean up these fields
              (1–3 min) — you can edit or save meanwhile, better values will
              merge in when ready.
            </div>
          )}
          <ProfileEditor profile={profile} setProfile={setProfile}
                         onSaved={() => setView('jobs')} />
        </>
      )}

      {view === 'jobs' && <JobsBoard />}
    </div>
  )
}
