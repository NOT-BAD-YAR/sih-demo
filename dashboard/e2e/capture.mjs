// Phase 7 E2E capture — drives the SOC dashboard in real Edge (headless),
// asserting each screen renders real data, works an incident end-to-end, and
// proves live polling updates without a page refresh.
//
// Run:  node e2e/capture.mjs   (requires API :8000 + Vite :5173 up + seeded DB)

import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(fileURLToPath(import.meta.url)) + '/..'      // dashboard/
const PROJECT = ROOT + '/..'                                       // repo root
const OUT = join(PROJECT, 'docs', 'verify_phase7_screens')
mkdirSync(OUT, { recursive: true })
const shot = (name) => join(OUT, `${name}.png`)
const PY = join(PROJECT, '.venv', 'Scripts', 'python.exe')

const base = 'http://localhost:5173'
let failures = 0
const check = (label, cond) => {
  if (cond) console.log(`  ✔ ${label}`)
  else {
    failures++
    console.log(`  ✘ FAIL ${label}`)
  }
}

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const waitText = (t, timeout = 15000) => page.waitForFunction((txt) => document.body.textContent.includes(txt), t, { timeout })

// Fresh demo state so the workflow always starts from an OPEN incident.
console.log('0) Reseeding demo data')
execFileSync(PY, ['-X', 'utf8', 'scripts/seed_demo.py'], { cwd: PROJECT, stdio: 'inherit' })

try {
  // ---------- 1. LOGIN (analyst) ----------
  console.log('1) Login')
  await page.goto(base + '/login')
  await page.fill('#username', 'analyst')
  await page.fill('#password', 'analyst')
  await page.click('button[type=submit]')
  await waitText('Open incidents')
  check('analyst signed in, Overview rendered', await page.locator('.stat-card').count() >= 4)
  await page.screenshot({ path: shot('01-overview'), fullPage: false })

  // ---------- 2. USERS + INVESTIGATION (rich baseline employee) ----------
  console.log('2) Users + Entity Investigation (EMP001)')
  await page.click('text=Users')
  await waitText('100 people')
  await page.fill('input[aria-label="Search users"]', 'EMP001')
  await waitText('People')
  await page.screenshot({ path: shot('02-users'), fullPage: false })
  await page.click('tr.clickable >> nth=0')
  await waitText('Normal behavior (baseline snapshot)')
  check('investigation shows baseline section', (await page.textContent('body')).includes('baseline snapshot'))
  const body = await page.textContent('body')
  check('investigation shows 30 windows', body.includes('30 windows'))
  check('investigation shows why-flagged card', body.includes('Why flagged?'))
  await page.screenshot({ path: shot('03-investigation-emp001'), fullPage: true })
  await page.click('text=Entities')
  await waitText('Entities')
  await page.screenshot({ path: shot('04-entities'), fullPage: false })

  // ---------- 3. INCIDENT #1 — full analyst workflow ----------
  console.log('3) Incident #1 — assign -> evidence -> actions -> note -> resolve')
  await page.click('text=Incidents')
  await waitText('Incidents')
  await page.click('tr.clickable >> nth=0')
  await waitText('Evidence replay')
  await waitText('Critical')
  check('incident detail rendered', (await page.textContent('body')).includes('Simulated response'))
  await page.screenshot({ path: shot('05-incident-open'), fullPage: false })

  await page.click('button:has-text("Assign")')
  await waitText('Assigned')
  await page.waitForTimeout(600)
  check('assign persisted (status pill Assigned)', (await page.textContent('body')).includes('Assigned'))

  await page.click('button:has-text("Investigate")')
  await waitText('Investigating')
  await page.waitForTimeout(600)
  check('investigate persisted (status pill Investigating)', (await page.textContent('body')).includes('Investigating'))

  await page.click('button:has-text("Force MFA")')
  await page.click('button:has-text("Isolate device")')
  await page.click('button:has-text("Revoke session")')
  await waitText('Revoke session')
  await page.waitForTimeout(800)
  await page.screenshot({ path: shot('06-incident-actions-evidence'), fullPage: true })
  const body2 = await page.textContent('body')
  check('evidence replay shows 3 contributing events', body2.includes('3 contributing events'))
  check('audit trail lists 3 actions', (body2.match(/applied/g) ?? []).length >= 3)

  await page.fill('textarea[aria-label="Add note"]', 'evidence reviewed, response complete')
  await page.click('button:has-text("Add note")')
  await waitText('evidence reviewed, response complete')
  check('note added', (await page.textContent('body')).includes('evidence reviewed, response complete'))

  await page.click('button:has-text("Resolve")')
  await waitText('Resolved')
  await page.screenshot({ path: shot('07-incident-resolved'), fullPage: false })
  check('incident resolved via UI', (await page.textContent('body')).includes('Resolved'))

  // ---------- 4. ALERTS ----------
  console.log('4) Alert queue — close one as false positive')
  await page.click('text=Alerts')
  await waitText('Alert queue')
  await page.screenshot({ path: shot('08-alerts'), fullPage: false })
  await page.click('button:has-text("False positive") >> nth=0')
  await page.waitForTimeout(1000)
  const body3 = await page.textContent('body')
  check('an alert closed as false positive', body3.includes('False positive'))

  // ---------- 5. LIVE UPDATE via polling (no refresh) ----------
  console.log('5) Live polling — new anomaly appears without refresh')
  await page.click('text=Overview')
  await waitText('Open incidents')
  await page.waitForFunction(() => {
    const cards = document.querySelectorAll('.stat-card')
    return cards.length >= 4 && /\d/.test(cards[1].textContent ?? '')
  })
  await page.waitForTimeout(500)
  const readCount = async () => {
    const text = (await page.locator('.stat-card').nth(1).textContent()) ?? ''
    const m = text.match(/(\d+)/)
    return m ? Number(m[1]) : -1
  }
  const before = await readCount()
  console.log(`  open alerts before: ${before}`)
  execFileSync(PY, ['-X', 'utf8', 'scripts/inject_live.py'], { cwd: ROOT + '/..', stdio: 'inherit' })
  await page.waitForFunction(
    (prev) => {
      const cards = document.querySelectorAll('.stat-card')
      if (cards.length < 2) return false
      const m = cards[1].textContent?.match(/(\d+)/)
      return m && Number(m[1]) > prev
    },
    before,
    { timeout: 40000 },
  )
  const after = await readCount()
  check(`open alerts rose ${before} -> ${after} on Overview without refresh`, after > before)
  await page.screenshot({ path: shot('09-overview-live-update'), fullPage: false })

  // ---------- 6. ADMIN (separate account) ----------
  console.log('6) Admin — create account + tune thresholds')
  await page.click('text=Sign out')
  await page.fill('#username', 'admin')
  await page.fill('#password', 'admin')
  await page.click('button[type=submit]')
  await waitText('Open incidents')
  await page.click('text=Admin')
  await waitText('Create analyst account')
  await page.fill('#new-user', 'soc2')
  await page.selectOption('#new-role', 'analyst')
  await page.fill('#new-password', 'pass1234')
  await page.click('button:has-text("Create")')
  await waitText('Account soc2 created')
  check('admin created account soc2', (await page.textContent('body')).includes('soc2'))
  await page.screenshot({ path: shot('10-admin-users'), fullPage: false })

  await page.click('button:has-text("Thresholds")')
  await waitText('Engine thresholds')
  await page.fill('#RISK_BAND_CRITICAL', '80')
  await page.click('button:has-text("Save thresholds")')
  await waitText('Thresholds updated')
  check('threshold tuned', (await page.textContent('body')).includes('Thresholds updated'))
  await page.screenshot({ path: shot('11-admin-thresholds'), fullPage: false })

  console.log(failures === 0 ? '\nALL E2E CHECKS PASSED' : `\n${failures} E2E CHECKS FAILED`)
} catch (e) {
  failures++
  console.error('E2E ERROR:', e.message)
  await page.screenshot({ path: shot('99-error'), fullPage: true }).catch(() => {})
} finally {
  await browser.close()
}

process.exit(failures === 0 ? 0 : 1)