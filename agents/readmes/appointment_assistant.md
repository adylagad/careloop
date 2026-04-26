# CareLoop Appointment Assistant

CareLoop Appointment Assistant helps an older adult or caregiver find real doctors,
clinics, and booking links without calling offices first.

## What It Does

- Understands natural appointment requests from ASI:One.
- Charges a small FET service fee before running live search.
- Uses Browser Use live booking search when available.
- Falls back to CMS NPPES public provider records plus booking handoff links.
- Shows visible appointment availability and cost only when a source publishes it.
- Keeps conversation state so follow-up questions use the last search.

## Example Prompts

```text
Find a primary care doctor near USC Village this week with Medicare.
Find a dermatologist near Westwood.
Which option is closest?
Can you write the next step for my daughter?
```

## Safety And Booking Boundary

This agent handles logistics, not diagnosis. It does not claim a confirmed appointment
unless a real booking API confirms the appointment. Without partner booking API
credentials, it prepares a booking handoff link where the patient or caregiver confirms
the slot, insurance, cost, and required patient information.
