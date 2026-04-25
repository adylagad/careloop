# 🧠 CareLoop — Autonomous Elderly Healthcare Orchestration Platform

## 1. Overview

CareLoop is an AI-powered multi-agent system built using Fetch.ai (uAgents), OmegaClaw, and Agent Payment Protocol to automate the entire U.S. healthcare journey for elderly patients.

It coordinates:
- Doctor appointments
- Prescription understanding
- Pharmacy fulfillment
- Medication adherence
- Caregiver communication
- Payments

The goal is to **eliminate operational friction in healthcare** and let seniors focus on recovery while agents handle logistics.

---

## 2. Problem Statement

Elderly patients struggle with:
- Navigating complex healthcare systems
- Understanding prescriptions
- Managing multiple medications
- Booking appointments
- Coordinating with caregivers
- Handling payments and insurance

Most existing solutions solve **one part**, not the **entire journey**.

---

## 3. Vision

> Build a fully autonomous healthcare coordination layer for seniors using multi-agent orchestration.

---

## 4. Goals (Hackathon Scope)

### Must Have (MVP)
- Care intake via chat
- Multi-agent orchestration (visible timeline)
- Appointment booking (mocked)
- Prescription understanding (mocked)
- Pharmacy comparison (mocked)
- Caregiver notifications

### Nice to Have
- Payment automation (Fetch.ai Payment Protocol)
- Medication reminders
- Delivery coordination

### Future Scope
- Real EHR integrations (Epic, Cerner)
- Insurance APIs
- Real pharmacy APIs

---

## 5. Users

### Primary
- Elderly patients (65+)

### Secondary
- Caregivers / family members

### Tertiary
- Clinics / pharmacies (future)

---

## 6. User Journey

1. User inputs request
2. Triage Agent evaluates urgency
3. Appointment Agent finds doctor
4. Booking confirmed
5. After visit → Prescription generated
6. Prescription Agent explains medication
7. Pharmacy Agent compares options
8. Payment Agent processes cost
9. Delivery/Pickup scheduled
10. Caregiver notified
11. Adherence Agent tracks medication
