# MediCore

**A Hospital Management System for Kenyan public hospitals — with SHA verification and M-Pesa integration.**



**Live demo:** https://medicore-web.onrender.com/
**Demo accounts:** `reception` · `nurse` · `doctor` · `lab` · `pharmacy` · `accounts` — password `medicore123` (all lowercase)

---

## Table of contents

1. [What it does](#what-it-does)
2. [Why it exists](#why-it-exists)
3. [Features](#features)
4. [Workflow](#workflow)
5. [Roles](#roles)
6. [Tech stack](#tech-stack)
7. [Screenshots](#screenshots)
8. [Getting started](#getting-started)
9. [Running locally](#running-locally)
10. [Environment variables](#environment-variables)
11. [Deployment](#deployment)
12. [Testing](#testing)
13. [Project structure](#project-structure)
14. [Data model](#data-model)
15. [Security & privacy](#security--privacy)
16. [Known limitations](#known-limitations)
17. [Roadmap](#roadmap)
18. [Author](#author)
19. [License](#license)

---

## What it does

MediCore is a full **outpatient department (OPD) management system** for a small Kenyan hospital. It covers the entire patient journey — from walking in the front door to walking out with a printed receipt — across six staff roles, on one shared queue board.

It replaces the paper-slip workflow that most Kenyan hospitals still use, but keeps the same physical hand-off model that staff are trained on: the patient still carries a slip, but now it has a **System ID** and **Queue Number** on it, and every counter can look up that patient in one keystroke.

It is **not** a full hospital information system — no inpatient wards, no theatre scheduling, no pharmacy inventory automation beyond basic stock tracking. It is a focused, working OPD loop.

---

## Why it exists

Three problems in Kenyan public hospitals motivated this project:

1. **SHA (Social Health Authority) verification is slow** — staff manually look up patients in the SHA portal, taking minutes per patient. MediCore automates the eligibility check via OTP and marks each patient as Active / Inactive / Pending / Unknown, so billing knows upfront whether to collect payment or claim.

2. **M-Pesa payment friction** — many hospitals take cash only, or send patients to an ATM. MediCore embeds **Safaricom Daraja STK Push** directly into the checkout counter: the pharmacist clicks "Collect", the patient's phone prompts for their PIN, the payment confirms, the receipt prints, the patient leaves.

3. **Paper slips get lost** — a patient carrying an illegible paper slip between the doctor and the pharmacy is a walking data-loss event. MediCore's slip has a scannable **System ID** (`MC-2026-0001`) and a **Queue Number** (`A-001`). Any counter can look the patient up by either, or by NID.

The project was scoped to a **proposal defense** — the goal is to demonstrate a credible, working, defensible system, not to ship to a real hospital.

---

## Features

### Patient journey
- **Registration with optional SHA verification** — two-step OTP (send code → confirm 6 digits). If the patient has no SHA, registration proceeds normally with `sha_status=unknown`.
- **Printable A5 patient slip** — System ID, queue number, name, age, sex, SHA status. No national ID or phone (privacy).
- **Role-aware identity card** — reception sees 6 fields (SID, NID, phone, age, sex, SHA); other roles see 3 fields (SID, age, sex).
- **Paper-slip model** — every counter has a **key-in lookup** page where staff type the SID, queue number, or NID and the patient's record opens instantly.

### Clinical workflow
- **Queue board** — kanban view filtered to the logged-in role's stage. Reception sees `reg`, nurse sees `nurse`, doctor sees `doctor`, etc.
- **Nurse vitals capture** — BP, temperature, weight, pulse. Saves and routes patient back to doctor.
- **Doctor consultation** — diagnosis, prescriptions with **drug autocomplete** (typing `para` suggests paracetamol with price), lab orders with **test autocomplete**, and role-aware action buttons to route the patient to lab / pharmacy / nurse.
- **Lab result entry** — one input per test, released back to the doctor with an **inbox notification**.
- **Pharmacy checkout** — prices any unpriced drugs, shows full bill, collects payment (M-Pesa STK Push or cash), dispenses, generates receipt.

### Billing & payments
- **Automatic bill line items** — registration, drugs (priced at prescribe time if the drug is in the catalogue), labs.
- **M-Pesa STK Push** — real integration via Safaricom Daraja. Falls back to mock mode when `DARAJA_CONSUMER_KEY` is empty, so the flow can be demonstrated offline.
- **M-Pesa C2B** — PayBill validation and confirmation callbacks.
- **Reversals** — accountant can reverse a successful M-Pesa transaction.
- **SHA claim coverage** — 70% on consultation and lab tests, 0% on registration fee and drugs. Co-payment amount shown to the patient on the receipt.
- **Receipts** — printable, generated as PDF (WeasyPrint → Supabase Storage on Render), with the SHA portion broken out.

### Financial & audit
- **Ledger** — all payments by date, method, and status.
- **SHA claims** — claim lifecycle: `draft → pending → under_review → approved → rejected → paid`.
- **Audit log** — every action is recorded with user, role, patient SID, IP, path, method, and timestamp. CSV export.
- **M-Pesa transaction log** — searchable by status, filterable.
- **Notification bell** — real-time counts per role (e.g. "3 lab results ready", "2 unpaid invoices").

### Non-clinical
- **Dark/light theme** — per-user preference persisted.
- **Responsive** — designed for desktop but the reception dashboard is usable on a tablet.
- **Demo seed command** — `python manage.py seed_demo` creates six demo users, groups, and a starter drug catalogue (24 drugs with Kenya-typical prices).

---

## Workflow

The full loop, end to end:       ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Reception │ │ Nurse │ │ Doctor │ │ Lab │
├──────────────┤ ├──────────────┤ ├──────────────┤ ├──────────────┤
│ 1. Register │───▶│ 2. Vitals │───▶│ 3. Consult │───▶│ 4. Results │
│ 2. SHA OTP │ │ BP/Temp │ │ Dx/Rx │ │ │
│ 3. Collect │ │ Weight │ │ Lab order │◀───│ 5. Release │
│ Ksh 200 │ │ Pulse │ │ │ └──────────────┘
│ 4. Print │ └──────────────┘ │ 6. Send to │
│ A5 slip │ │ Pharmacy │
└──────────────┘ └──────────────┘
│
▼
┌──────────────┐
│ Pharmacy │
├──────────────┤
│ 7. Price Rx │
│ 8. Collect │
│ M-Pesa │
│ 9. Dispense │
│10. Receipt │
│11. Patient │
│ → History │
└──────────────┘

text

Every counter has a **lookup page** where the patient's System ID or queue number can be typed to jump straight to their visit. The patient physically carries the slip — MediCore does not require the patient to stay with any one device.

**The accountant** sees a read-only view of all counters, the ledger, the SHA claims, and the M-Pesa transaction log — for reconciliation.

---

## Roles

| Role | Home | Lookup page | Can do |
|---|---|---|---|
| **Reception** | `/` | `/patients/` | Register patients, verify SHA, collect Ksh 200 fee, print slips |
| **Nurse** | `/` | `/nurse/lookup/` | Record vitals, route back to doctor |
| **Doctor** | `/` | `/doctor/lookup/` | Consult, diagnose, prescribe, order labs, route to lab/pharmacy/nurse |
| **Lab** | `/` | `/lab/lookup/` | Enter results, release back to doctor |
| **Pharmacy** | `/` | `/pharmacy/lookup/` | Price drugs, dispense, collect payment, print receipt |
| **Accounts** | `/` | — | View ledger, SHA claims, M-Pesa log, audit log |

Login redirects each role to their own landing page: doctor → doctor lookup, nurse → nurse lookup, lab → lab lookup, pharmacy → pharmacy lookup. Reception and accounts land on their dashboard.

**Role is enforced server-side** via a `@role_required(...)` decorator on every view. Cross-role attempts return 403 or redirect to the caller's own home.

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| **Language** | Python 3.14 | Modern, matches the year |
| **Web framework** | Django 5.2 | Batteries included: ORM, admin, auth, forms, CSRF |
| **Database (prod)** | PostgreSQL 18 (Render) | Free tier, reliable, per-role grants |
| **Database (dev)** | SQLite | Zero-setup local development |
| **Cache / broker** | Redis (Render) | For Celery and future AI phases |
| **Async tasks** | Celery | For M-Pesa callbacks (can run eager in dev) |
| **PDF** | WeasyPrint | HTML-to-PDF for receipts |
| **Payments** | Safaricom Daraja (STK Push + C2B) | Direct integration, no third-party wrapper |
| **SHA** | MockProvider (demo) | Real SHA HMIA has no public API |
| **File storage** | Supabase Storage (prod), local `media/` (dev) | PDFs persist across deploys |
| **Hosting** | Render | Free tier, Blueprint-deployable |
| **CI** | GitHub Actions | Run tests on every push |

---

## Screenshots

> Screenshots are placeholders in this repository. See [`docs/screenshots/`](docs/screenshots/) for the current set. They will be added after the defense.

| Screen | Description |
|---|---|
| `01-reception-dashboard.png` | Registration card, today's queue, KPI counters |
| `02-patient-slip.png` | Printable A5 slip with System ID and queue number |
| `03-queue-board.png` | Kanban with role-filtered columns |
| `04-doctor-consultation.png` | Doctor workbench with autocomplete |
| `05-lab-inbox.png` | Lab inbox with released results |
| `06-pharmacy-checkout.png` | Bill, M-Pesa panel, Collect button |
| `07-receipt.png` | Printable receipt with SHA breakdown |
| `08-sha-claims.png` | SHA claim drawer with lifecycle timeline |
| `09-audit-log.png` | Audit log with CSV export |

---

## Getting started

### Prerequisites

- Python 3.13 or 3.14 (tested on 3.14.3; Python 3.13 is recommended for production due to some library compatibility)
- Git
- A virtual environment (see below)

### Clone the repository

```bash
git clone https://github.com/Edufel45/medicore.git
cd medicore/backend
