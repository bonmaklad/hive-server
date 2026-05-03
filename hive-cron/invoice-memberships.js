import 'dotenv/config'
import { createClient } from '@supabase/supabase-js'

/* =========================
   Helpers
========================= */

function requireEnv(name) {
  const v = process.env[name]
  if (!v) throw new Error(`Missing ${name}`)
  return v
}

// NZ-safe YYYY-MM-DD (no timezone drift)
function nzTodayISO() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Pacific/Auckland' }).format(new Date())
}

// Add months safely for NZ billing periods (avoid Date parsing drift)
function addMonthsISO(iso, months) {
  const [y, m, d] = iso.split('-').map(Number)
  // construct in UTC to avoid local timezone shifting the date
  const dt = new Date(Date.UTC(y, m - 1, d))
  dt.setUTCMonth(dt.getUTCMonth() + months)
  return dt.toISOString().slice(0, 10)
}

function toInt(v) {
  const n = Number(v)
  return Number.isFinite(n) ? Math.floor(n) : 0
}

function nicePlanLabel(plan) {
  const p = (plan || '').toString().toLowerCase()
  if (p.includes('office')) return 'Hive Office'
  if (p.includes('desk')) return 'Hive Desk'
  if (p.includes('hot')) return 'Hive Hot Desk'
  if (p.includes('storage')) return 'Hive Storage'
  return 'Hive Membership'
}

// Credits: tokens = round(net (ex-GST) monthly invoice dollars / 30), capped at 40
function calcMonthlyTokens(amountExGstCents) {
  const dollars = amountExGstCents / 100
  const tokens = Math.round(dollars / 30)
  return Math.max(0, Math.min(40, tokens))
}

async function stripeRequest(method, path, params = {}) {
  const res = await fetch(`https://api.stripe.com/v1${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${requireEnv('STRIPE_SECRET_KEY')}`,
      'Stripe-Version': '2024-06-20',
      'Content-Type': 'application/x-www-form-urlencoded'
    },
    body: method === 'GET' ? undefined : new URLSearchParams(params)
  })

  const json = await res.json()
  if (!res.ok) throw new Error(json?.error?.message || 'Stripe error')
  return json
}

async function postToTeams(message) {
  const url = process.env.TEAMS_WEBHOOK_URL
  if (!url) return

  await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: 'Hive Cron',
      email: 'cron@hivehq.nz',
      message
    })
  })
}

/* =========================
   Main
========================= */

async function run() {
  requireEnv('SUPABASE_URL')
  requireEnv('SUPABASE_SERVICE_ROLE_KEY')
  requireEnv('STRIPE_SECRET_KEY')

  const dryRun = process.env.DRY_RUN === '1'
  const todayISO = nzTodayISO()
  const invoiceDay = Number(todayISO.slice(-2))

  console.log(`[hive-cron] start ${todayISO} dryRun=${dryRun}`)

  const supabase = createClient(
    process.env.SUPABASE_URL,
    process.env.SUPABASE_SERVICE_ROLE_KEY,
    { auth: { persistSession: false } }
  )

  const LOCK_KEY = 9142025
  const { data: locked } = await supabase.rpc('pg_try_advisory_lock', { key: LOCK_KEY })
  if (locked !== true) return

  let created = 0
  let skipped = 0
  let failed = 0

  try {
    const { data: memberships, error } = await supabase
      .from('memberships')
      .select('*')
      .eq('status', 'live')
      .eq('next_invoice_at', invoiceDay)

    if (error) throw error

    for (const m of memberships ?? []) {
      try {
        // payment terms logic
        if (m.payment_terms === 'advanced') {
          if (!m.paid_till || m.paid_till >= todayISO) {
            skipped++
            continue
          }
          if (!dryRun) {
            const { error: upErr } = await supabase
              .from('memberships')
              .update({ payment_terms: 'invoice' })
              .eq('id', m.id)
            if (upErr) throw upErr
          }
        } else if (m.payment_terms !== 'invoice') {
          skipped++
          continue
        }

        // monthly_amount_cents is EX-GST (Stripe Tax will calculate GST)
        const amount = toInt(m.monthly_amount_cents)
        if (amount <= 0) {
          skipped++
          continue
        }

        // tenant lookup (KEEPING your working approach)
        const { data: tu, error: tuErr } = await supabase
          .from('tenant_users')
          .select('tenant_id')
          .eq('user_id', m.owner_id)
          .single()

        if (tuErr) throw tuErr
        if (!tu?.tenant_id) throw new Error('No tenant found')

        // prevent double-invoice (Supabase record)
        const { data: existing, error: exErr } = await supabase
          .from('invoices')
          .select('id')
          .eq('membership_id', m.id)
          .eq('issued_on', todayISO)
          .maybeSingle()

        if (exErr) throw exErr

        if (existing) {
          skipped++
          continue
        }

        const periodFrom = todayISO
        const periodTo = addMonthsISO(todayISO, 1)

        const label = nicePlanLabel(m.plan)
        const lineDesc = `${label} – ${m.plan}\nPeriod: ${periodFrom} to ${periodTo}`

        if (dryRun) {
          created++
          continue
        }

        // find Stripe customer by tenant_id metadata (KEEPING what worked)
        const customers = await stripeRequest('GET', '/customers?limit=100')
        const customer = customers.data.find(c => c.metadata?.tenant_id === tu.tenant_id)
        if (!customer) throw new Error('Stripe customer not found')

        // create invoice (Stripe Tax ON + show NZ GST tax ID)
        const invoice = await stripeRequest('POST', '/invoices', {
          customer: customer.id,
          collection_method: 'send_invoice',
          days_until_due: '0',
          auto_advance: 'true',
          'automatic_tax[enabled]': 'true',
          description: `${label} – ${m.plan}`,
          'account_tax_ids[0]': 'txi_1ShJGiC0FFvJocjApaUjzGPU',
          'metadata[membership_id]': m.id,
          'metadata[tenant_id]': tu.tenant_id,
          'metadata[period_from]': periodFrom,
          'metadata[period_to]': periodTo
        })

        // invoice item (EX-GST amount)
        await stripeRequest('POST', '/invoiceitems', {
          customer: customer.id,
          invoice: invoice.id,
          amount,
          currency: 'nzd',
          description: lineDesc
        })

        // send invoice
        await stripeRequest('POST', `/invoices/${invoice.id}/send`)

        // INSERT into Supabase invoices table (MATCHES YOUR SCHEMA)
        const { error: insErr } = await supabase
          .from('invoices')
          .insert({
            owner_id: m.owner_id,        // REQUIRED
            membership_id: m.id,
            invoice_number: invoice.id,  // UNIQUE
            amount_cents: amount,
            currency: 'NZD',
            status: 'open',
            issued_on: todayISO,
            due_on: todayISO             // days_until_due = 0
          })

        if (insErr) throw insErr

        // Upsert monthly room credits for this owner and billing period
        // Rule: 10 tokens per $200 (incl GST) per month
        const periodStart = todayISO
        const tokens = calcMonthlyTokens(amount)
        if (tokens > 0) {
          // preserve tokens_used if a record already exists for this period
          const { data: existingCredits, error: existingCreditsErr } = await supabase
            .from('room_credits')
            .select('tokens_used')
            .eq('owner_id', m.owner_id)
            .eq('period_start', periodStart)
            .maybeSingle()

          if (existingCreditsErr) throw existingCreditsErr

          const { error: upsertCreditsErr } = await supabase
            .from('room_credits')
            .upsert({
              owner_id: m.owner_id,
              period_start: periodStart,
              tokens_total: tokens,
              tokens_used: Math.max(0, existingCredits?.tokens_used || 0)
            }, { onConflict: 'owner_id,period_start' })

          if (upsertCreditsErr) throw upsertCreditsErr
        }

        created++
      } catch (err) {
        failed++
        console.error('[fail]', m.id, err.message || err)
      }
    }
  } finally {
    await supabase.rpc('pg_advisory_unlock', { key: LOCK_KEY })
  }

  const summary = `[hive-cron] complete created=${created} skipped=${skipped} failed=${failed}`
  console.log(summary)

  // only ping Teams if something happened
  if (created > 0 || failed > 0) {
    await postToTeams(summary)
  }
}

run().catch(() => process.exit(1))
