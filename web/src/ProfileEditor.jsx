import React, { useState } from 'react'

const JOB_TYPES = ['contract', 'freelance', 'part-time', 'full-time']
const REMOTE_TYPES = ['remote', 'hybrid', 'on-site']
const CURRENCIES = ['USD', 'INR', 'EUR', 'GBP']
const PERIODS = ['hour', 'month', 'year', 'project']

function Chips({ items, onChange, placeholder }) {
  const [draft, setDraft] = useState('')
  function add() {
    const v = draft.trim()
    if (v && !items.includes(v)) onChange([...items, v])
    setDraft('')
  }
  return (
    <div className="chips">
      {items.map((s, i) => (
        <span key={i} className="chip">
          {typeof s === 'string' ? s : s.name || s.degree || JSON.stringify(s)}
          <button onClick={() => onChange(items.filter((_, j) => j !== i))}>×</button>
        </span>
      ))}
      <input value={draft} placeholder={placeholder}
             onChange={e => setDraft(e.target.value)}
             onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), add())}
             onBlur={add} />
    </div>
  )
}

function Toggles({ options, selected, onChange }) {
  return (
    <div className="toggles">
      {options.map(o => (
        <button key={o}
                className={selected.includes(o) ? 'on' : ''}
                onClick={() => onChange(selected.includes(o)
                  ? selected.filter(x => x !== o) : [...selected, o])}>
          {o}
        </button>
      ))}
    </div>
  )
}

export default function ProfileEditor({ profile, setProfile, onSaved }) {
  const [saving, setSaving] = useState(false)
  const [links, setLinks] = useState({})       // {github: {status,message}, ...}
  const [checking, setChecking] = useState(false)
  const p = profile
  const prefs = p.preferences || {}
  const set = (k, v) => setProfile({ ...p, [k]: v })
  const setPref = (k, v) => setProfile({ ...p, preferences: { ...prefs, [k]: v } })

  async function verifyLinks() {
    setChecking(true)
    try {
      const res = await fetch('/api/links/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ github: p.github || '', linkedin: p.linkedin || '' }),
      }).then(r => r.json())
      setLinks(res)
      // adopt the cleaned-up URLs
      const next = { ...p }
      if (res.github?.url && res.github.status !== 'invalid') next.github = res.github.url
      if (res.linkedin?.url && res.linkedin.status !== 'invalid') next.linkedin = res.linkedin.url
      setProfile(next)
    } catch (e) {
      setLinks({ error: { status: 'invalid', message: String(e) } })
    } finally {
      setChecking(false)
    }
  }

  const linkNote = key => {
    const r = links[key]
    if (!r) return null
    const cls = r.status === 'valid' ? 'ok' : r.status === 'invalid' ? 'bad' : 'warn'
    return (
      <div className={`link-note ${cls}`}>
        {r.message}
        {r.status !== 'invalid' && r.url &&
          <a href={r.url} target="_blank" rel="noreferrer"> Open ↗</a>}
      </div>
    )
  }

  async function save() {
    setSaving(true)
    await fetch('/api/profile', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(p),
    })
    setSaving(false)
    onSaved()
  }

  return (
    <main className="editor">
      <div className="editor-head">
        <h2>Your job profile</h2>
        <span className="badge">{p.parse_engine === 'heuristics+ollama' ? 'parsed with local AI' : p.parse_engine === 'heuristics' ? 'parsed with heuristics — review carefully' : 'manual'}</span>
      </div>

      <section>
        <h3>Basics</h3>
        <div className="grid2">
          <label>Name<input value={p.name || ''} onChange={e => set('name', e.target.value)} /></label>
          <label>Headline / title<input value={p.title || ''} onChange={e => set('title', e.target.value)} placeholder="Full Stack Engineer — Node.js | React | AWS" /></label>
          <label>Email<input value={p.email || ''} onChange={e => set('email', e.target.value)} /></label>
          <label>Phone<input value={p.phone || ''} onChange={e => set('phone', e.target.value)} /></label>
          <label>Location<input value={p.location || ''} onChange={e => set('location', e.target.value)} /></label>
          <label>Years of experience<input type="number" step="0.5" value={p.years_experience ?? ''} onChange={e => set('years_experience', e.target.value ? parseFloat(e.target.value) : null)} /></label>
          <label>GitHub
            <input value={p.github || ''} placeholder="github.com/username"
                   onChange={e => set('github', e.target.value)} />
            {linkNote('github')}
          </label>
          <label>LinkedIn
            <input value={p.linkedin || ''} placeholder="linkedin.com/in/your-handle"
                   onChange={e => set('linkedin', e.target.value)} />
            {linkNote('linkedin')}
          </label>
        </div>
        <button className="add verify" disabled={checking} onClick={verifyLinks}>
          {checking ? 'Checking…' : '🔗 Verify & clean up links'}
        </button>
        <label>Summary<textarea rows={3} value={p.summary || ''} onChange={e => set('summary', e.target.value)} /></label>
      </section>

      <section>
        <h3>Skills</h3>
        <Chips items={p.skills || []} onChange={v => set('skills', v)} placeholder="add skill + Enter" />
      </section>

      <section>
        <h3>Work experience</h3>
        {(p.experience || []).map((ex, i) => (
          <div className="exp-item" key={i}>
            <div className="row-item">
              <input value={ex.company || ''} placeholder="Company"
                     onChange={e => set('experience', p.experience.map((x, j) => j === i ? { ...x, company: e.target.value } : x))} />
              <input value={ex.title || ''} placeholder="Designation / role"
                     onChange={e => set('experience', p.experience.map((x, j) => j === i ? { ...x, title: e.target.value } : x))} />
              <input className="yr" value={ex.period || ''} placeholder="Period"
                     onChange={e => set('experience', p.experience.map((x, j) => j === i ? { ...x, period: e.target.value } : x))} />
              <button className="del" onClick={() => set('experience', p.experience.filter((_, j) => j !== i))}>×</button>
            </div>
            <textarea rows={3} value={ex.description || ''} placeholder="What you did there (from your resume)"
                      onChange={e => set('experience', p.experience.map((x, j) => j === i ? { ...x, description: e.target.value } : x))} />
          </div>
        ))}
        <button className="add" onClick={() => set('experience', [...(p.experience || []), { company: '', title: '', period: '', description: '' }])}>+ experience</button>
      </section>

      <section>
        <h3>Internships / Projects</h3>
        {(p.projects || []).map((pr, i) => (
          <div className="exp-item" key={i}>
            <div className="row-item">
              <input value={pr.name || ''} placeholder="Project or 'Role, Company' for internships"
                     onChange={e => set('projects', p.projects.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
              <input className="yr" value={pr.period || ''} placeholder="Period"
                     onChange={e => set('projects', p.projects.map((x, j) => j === i ? { ...x, period: e.target.value } : x))} />
              <button className="del" onClick={() => set('projects', p.projects.filter((_, j) => j !== i))}>×</button>
            </div>
            <textarea rows={2} value={pr.description || ''} placeholder="Description (from your resume)"
                      onChange={e => set('projects', p.projects.map((x, j) => j === i ? { ...x, description: e.target.value } : x))} />
          </div>
        ))}
        <button className="add" onClick={() => set('projects', [...(p.projects || []), { name: '', period: '', description: '' }])}>+ entry</button>
      </section>

      <section>
        <h3>Education</h3>
        {(p.education || []).map((ed, i) => (
          <div className="row-item" key={i}>
            <input value={ed.degree || ''} placeholder="Degree"
                   onChange={e => set('education', p.education.map((x, j) => j === i ? { ...x, degree: e.target.value } : x))} />
            <input value={ed.institution || ''} placeholder="Institution"
                   onChange={e => set('education', p.education.map((x, j) => j === i ? { ...x, institution: e.target.value } : x))} />
            <input className="yr" value={ed.year || ''} placeholder="Year"
                   onChange={e => set('education', p.education.map((x, j) => j === i ? { ...x, year: e.target.value } : x))} />
            <button className="del" onClick={() => set('education', p.education.filter((_, j) => j !== i))}>×</button>
          </div>
        ))}
        <button className="add" onClick={() => set('education', [...(p.education || []), { degree: '', institution: '', year: '' }])}>+ education</button>
      </section>

      <section>
        <h3>Certifications</h3>
        <Chips items={p.certifications || []} onChange={v => set('certifications', v)} placeholder="add certification + Enter" />
      </section>

      <section className="prefs">
        <h3>Job preferences <span className="hint">(hard filters — jobs violating these never reach you)</span></h3>
        <label className="lbl">Engagement type</label>
        <Toggles options={JOB_TYPES} selected={prefs.job_types || []} onChange={v => setPref('job_types', v)} />
        <label className="lbl">Work mode</label>
        <Toggles options={REMOTE_TYPES} selected={prefs.remote_types || []} onChange={v => setPref('remote_types', v)} />
        <label className="lbl">Locations you'll take</label>
        <Chips items={prefs.locations || []} onChange={v => setPref('locations', v)} placeholder="e.g. Anywhere (Remote), India, US-overlap" />
        <label className="lbl">Pay floor</label>
        <div className="salary-row">
          <select value={prefs.salary_currency || 'USD'} onChange={e => setPref('salary_currency', e.target.value)}>
            {CURRENCIES.map(c => <option key={c}>{c}</option>)}
          </select>
          <input type="number" placeholder="min" value={prefs.salary_min ?? ''}
                 onChange={e => setPref('salary_min', e.target.value ? +e.target.value : null)} />
          <span>—</span>
          <input type="number" placeholder="max (optional)" value={prefs.salary_max ?? ''}
                 onChange={e => setPref('salary_max', e.target.value ? +e.target.value : null)} />
          <span>per</span>
          <select value={prefs.salary_period || 'hour'} onChange={e => setPref('salary_period', e.target.value)}>
            {PERIODS.map(c => <option key={c}>{c}</option>)}
          </select>
        </div>
        <label className="lbl">Minimum company rating (Glassdoor-style, optional)</label>
        <input className="rating" type="number" min="1" max="5" step="0.1" value={prefs.min_company_rating ?? ''}
               onChange={e => setPref('min_company_rating', e.target.value ? +e.target.value : null)} />
        <label className="lbl">Must-have keywords</label>
        <Chips items={prefs.keywords || []} onChange={v => setPref('keywords', v)} placeholder="e.g. Node.js, CouchDB" />
      </section>

      <div className="save-bar">
        <button className="cta" disabled={saving} onClick={save}>
          {saving ? 'Saving…' : 'Save profile & find jobs →'}
        </button>
      </div>
    </main>
  )
}
