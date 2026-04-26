import sys
import os
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"
sys.path.insert(0, str(AGENTS))
os.environ["BROWSER_USE_API_KEY"] = ""

from domain import (  # noqa: E402
    APPOINTMENT_SERVICE_FEE_FET,
    _mock_otc_catalog,
    appointment_unpaid_result,
    build_appointment_payment_quote,
    build_appointment_search_quote,
    build_otc_order_quote,
    build_pharmacy_fulfillment_status,
    explain_prescription_document,
    explain_prescription,
    format_appointment_search_preview,
    format_otc_order_preview,
    is_otc_order_intent,
    is_appointment_intent,
    is_pharmacy_status_intent,
    notify_caregiver,
    notify_caregiver_from_result,
    otc_order_paid_result,
    orchestrate_care,
    pharmacy_monitoring_result,
    pharmacy_status_update_result,
    pharmacy_unpaid_result,
    triage_emergency_reason,
    triage_request,
    triage_route,
)
from models import AppointmentOption, AppointmentSearchQuote, CareRequest, PaymentQuote, PrescriptionDocumentRequest  # noqa: E402
from appointment_agent import (  # noqa: E402
    APPOINTMENT_CONTEXT_BY_SENDER,
    PAYMENT_REQUEST_VERSION as APPOINTMENT_PAYMENT_REQUEST_VERSION,
    PendingAppointmentPayment,
    _pending_requires_refresh as appointment_pending_requires_refresh,
    _request_fingerprint as appointment_request_fingerprint,
    appointment_chat_response,
)
from appointment_data import infer_appointment_specialty, infer_location, parse_browser_appointment_text  # noqa: E402
from browser_cache import browser_cache_key, cached_browser_call  # noqa: E402
from caregiver_agent import CAREGIVER_CONTEXT_BY_SENDER, caregiver_chat_response  # noqa: E402
from pharmacy_agent import PHARMACY_CONTEXT_BY_SENDER, pharmacy_chat_response  # noqa: E402
from pharmacy_agent import PAYMENT_REQUEST_VERSION, PendingOrderPayment, _load_pending_by_sender, _pending_payment_message, _pending_requires_refresh, _request_fingerprint, _store_pending, pending_by_sender, pending_orders  # noqa: E402
from pharmacy_data import _parse_browser_price_text  # noqa: E402
from prescription_agent import PRESCRIPTION_CONTEXT_BY_SENDER, prescription_chat_response  # noqa: E402
from triage_agent import TRIAGE_CONTEXT_BY_SENDER, triage_chat_response  # noqa: E402
from orchestrator_agent import ORCHESTRATOR_CONTEXT_BY_SENDER, OrchestratorSession, orchestrator_chat_response  # noqa: E402


class AgentLogicTests(unittest.TestCase):
    def make_request(self, text: str, context=None) -> CareRequest:
        return CareRequest(
            case_id="case-test-001",
            user_id="user-test",
            text=text,
            context=context,
        )

    def test_pharmacy_fulfillment_status_has_fet_quote(self):
        request = self.make_request(
            "Is my prescription ready at CVS Westwood for delivery?",
            {
                "location": "Los Angeles, CA",
                "preference": "delivery",
                "pharmacy_name": "CVS Pharmacy - Westwood Blvd",
                "medication": "Atorvastatin",
                "dosage": "20 mg",
            },
        )
        status = build_pharmacy_fulfillment_status(request)

        self.assertEqual(status.medication, "Atorvastatin")
        self.assertEqual(status.status, "ready_for_delivery")
        self.assertEqual(status.payment_quote.currency, "FET")
        self.assertEqual(status.payment_quote.payment_method, "fet_direct")

    def test_pharmacy_monitoring_results(self):
        request = self.make_request(
            "Keep checking whether my prescription is ready at CVS Westwood for pickup",
            {
                "location": "Los Angeles, CA",
                "preference": "pickup",
                "pharmacy_name": "CVS Pharmacy - Westwood Blvd",
            },
        )
        status = build_pharmacy_fulfillment_status(request)

        paid = pharmacy_monitoring_result(request, status)
        update = pharmacy_status_update_result(
            request,
            build_pharmacy_fulfillment_status(request, monitor_tick=2),
        )
        unpaid = pharmacy_unpaid_result(request, "demo reject")

        self.assertEqual(paid.status, "monitoring")
        self.assertEqual(update.status, "ready_for_pickup")
        self.assertTrue(any("Payment completed" in event for event in paid.timeline_events))
        self.assertEqual(unpaid.status, "payment_required")
        self.assertIn("active automatic monitoring", unpaid.summary)

    def test_pharmacy_status_can_infer_hidden_pending_prescription(self):
        request = self.make_request("Is my prescription ready at CVS Westwood?")
        status = build_pharmacy_fulfillment_status(request)

        self.assertEqual(status.medication, "Metformin")
        self.assertEqual(status.dosage, "500 mg")
        self.assertEqual(status.status, "in_progress")

    def test_pharmacy_assistant_handles_otc_order_quote(self):
        request = self.make_request(
            "Find the best allergy medicine near Westwood and order it for delivery",
            {"location": "Westwood, Los Angeles, CA", "preference": "delivery"},
        )
        order = build_otc_order_quote(request)
        preview = format_otc_order_preview(order)
        paid = otc_order_paid_result(request, order)

        self.assertTrue(is_otc_order_intent(request.text))
        self.assertEqual(order.product.active_ingredient, "Loratadine")
        self.assertEqual(order.payment_quote.currency, "FET")
        self.assertIn("Other options considered", preview)
        self.assertIn("Price comparison found", preview)
        self.assertIn("Offline pickup options", preview)
        self.assertIn("Checkout handoff", preview)
        self.assertEqual(paid.status, "order_ready_for_checkout")
        self.assertIn("https://www.costplusdrugs.com", paid.summary)

    def test_pharmacy_assistant_separates_prescription_status_intent(self):
        self.assertTrue(is_pharmacy_status_intent("Is my prescription ready at CVS Westwood?"))
        self.assertFalse(is_otc_order_intent("Is my prescription ready at CVS Westwood?"))

    def test_pharmacy_assistant_answers_followup_from_last_otc_context(self):
        sender = "otc-followup-user"
        PHARMACY_CONTEXT_BY_SENDER.pop(sender, None)

        request = self.make_request(
            "Find the best allergy medicine near Westwood and order it for delivery",
            {"location": "Westwood, Los Angeles, CA", "preference": "delivery"},
        )
        PHARMACY_CONTEXT_BY_SENDER[sender] = build_otc_order_quote(request)

        followup = pharmacy_chat_response(
            None,
            sender,
            "which is the nearest store to USC Village where I can collect it? I do not want online",
        )

        self.assertIn("USC Village", followup)
        self.assertIn("Loratadine", followup)
        self.assertNotIn("I can help with over-the-counter medicine only", followup)

    def test_pharmacy_assistant_requests_payment_before_live_quote(self):
        sender = "otc-payment-first-user"
        PHARMACY_CONTEXT_BY_SENDER.pop(sender, None)

        first = pharmacy_chat_response(
            None,
            sender,
            "Find the best allergy medicine near Westwood and order it for delivery",
        )

        self.assertIn("Service fee required", first)
        self.assertIn("0.1 FET", first)
        self.assertNotIn("Price comparison found", first)

    def test_pharmacy_chat_reuses_pending_payment(self):
        sender = "otc-pending-user"
        PHARMACY_CONTEXT_BY_SENDER.pop(sender, None)
        pending_by_sender.pop(sender, None)
        for reference, pending in list(pending_orders.items()):
            if pending.original_sender == sender:
                pending_orders.pop(reference, None)

        request = self.make_request(
            "Find the best allergy medicine near Westwood and order it for delivery",
            {"location": "Westwood, Los Angeles, CA", "preference": "delivery"},
        )
        quote = build_otc_order_quote(request).payment_quote
        pending = PendingOrderPayment(
            original_sender=sender,
            request=request,
            quote=quote,
            response_channel="chat",
            request_fingerprint=_request_fingerprint(request),
            created_at=9999999999,
            request_version=PAYMENT_REQUEST_VERSION,
        )

        _store_pending(None, pending)
        loaded = _load_pending_by_sender(None, sender)
        second = _pending_payment_message(loaded)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.quote.reference, quote.reference)
        self.assertIn("I resent the same Pay option", second)

    def test_pharmacy_chat_refreshes_legacy_pending_payment(self):
        request = self.make_request(
            "Find the best allergy medicine near UCLA",
            {"location": "UCLA", "preference": "pickup"},
        )
        current_quote = build_otc_order_quote(request).payment_quote
        legacy_quote = PaymentQuote(**{**current_quote.dict(), "amount": "0.05"})
        pending = PendingOrderPayment(
            original_sender="otc-legacy-user",
            request=request,
            quote=legacy_quote,
            response_channel="chat",
            request_fingerprint=_request_fingerprint(request),
            created_at=9999999999,
            request_version="legacy",
        )

        self.assertTrue(_pending_requires_refresh(pending, _request_fingerprint(request)))

    def test_pharmacy_chat_refreshes_old_payment_request_version(self):
        request = self.make_request(
            "Find the best allergy medicine near UCLA",
            {"location": "UCLA", "preference": "pickup"},
        )
        quote = build_otc_order_quote(request).payment_quote
        pending = PendingOrderPayment(
            original_sender="otc-legacy-version-user",
            request=request,
            quote=quote,
            response_channel="chat",
            request_fingerprint=_request_fingerprint(request),
            created_at=9999999999,
            request_version="legacy",
        )

        self.assertTrue(_pending_requires_refresh(pending, _request_fingerprint(request)))

    def test_browser_use_price_text_parser(self):
        product = next(item for item in _mock_otc_catalog() if item.active_ingredient == "Loratadine")
        parsed = _parse_browser_price_text(
            "- Walmart — $7.71 — Shipping or pickup — https://www.walmart.com/example\n"
            "- Target — price not confirmed — Pickup — https://www.target.com/example\n"
            "- GoodRx — $2.00 — Coupon price — https://www.goodrx.com/loratadine",
            product,
        )

        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].merchant, "Walmart")
        self.assertEqual(parsed[1].price_usd, "$2.00")

    def test_caregiver_notifier_drafts_sms_update(self):
        result = notify_caregiver(
            self.make_request(
                "Dad's OTC allergy medicine checkout is ready and needs confirmation.",
                {"caregiver": "daughter", "patient_name": "Dad", "channel": "sms"},
            )
        )

        self.assertEqual(result.agent_name, "careloop-caregiver-notifier")
        self.assertEqual(result.status, "action_needed")
        self.assertIn("daughter", result.summary)
        self.assertIn("Dad", result.summary)
        self.assertIn("confirm", result.summary.lower())

    def test_caregiver_notifier_marks_urgent_alert(self):
        result = notify_caregiver(
            self.make_request(
                "Mom has chest pain and cannot breathe",
                {"caregiver": "son", "patient_name": "Mom"},
            )
        )

        self.assertEqual(result.status, "urgent")
        self.assertIn("URGENT", result.summary)
        self.assertIn("emergency", result.summary.lower())

    def test_caregiver_notifier_accepts_care_result(self):
        source = self.make_request("Appointment booked")
        care_result = notify_caregiver(source)
        update = notify_caregiver_from_result(
            care_result,
            {"caregiver": "family caregiver", "patient_name": "the patient", "channel": "email", "urgency": "info"},
        )

        self.assertIn("Subject:", update.summary)
        self.assertIn("Source agent", update.summary)

    def test_caregiver_chat_uses_followup_context(self):
        sender = "caregiver-followup-user"
        CAREGIVER_CONTEXT_BY_SENDER.pop(sender, None)

        first = caregiver_chat_response(
            None,
            sender,
            "Write an SMS to my daughter that Dad's allergy medicine checkout is ready.",
        )
        second = caregiver_chat_response(None, sender, "make it an email to my son instead")

        self.assertIn(sender, CAREGIVER_CONTEXT_BY_SENDER)
        self.assertIn("Dad", second)
        self.assertIn("son", second.lower())
        self.assertIn("Subject:", second)
        self.assertNotEqual(first, second)

    def test_caregiver_chat_greeting_explains_agent(self):
        response = caregiver_chat_response(None, "caregiver-hi-user", "hi")

        self.assertIn("CareLoop Caregiver Notifier", response)
        self.assertIn("SMS", response)
        self.assertNotIn("the patient has a care coordination update", response)

    def test_appointment_assistant_requests_payment_before_live_search(self):
        sender = "appointment-payment-user"
        APPOINTMENT_CONTEXT_BY_SENDER.pop(sender, None)

        response = appointment_chat_response(
            None,
            sender,
            "Find a primary care doctor near USC Village this week with Medicare.",
        )

        self.assertTrue(is_appointment_intent("Find a primary care doctor near USC Village"))
        self.assertIn("Service fee required", response)
        self.assertIn(f"{APPOINTMENT_SERVICE_FEE_FET} FET", response)
        self.assertNotIn("Real appointment options found", response)

    def test_browser_appointment_text_parser(self):
        parsed = parse_browser_appointment_text(
            "- UCLA Health Primary Care — Family Medicine — 200 UCLA Medical Plaza, Los Angeles — "
            "Tomorrow 10:30 AM — not published — https://www.uclahealth.org/appointments\n"
            "- Bad row — missing — fields"
        )

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].provider_name, "UCLA Health Primary Care")
        self.assertEqual(parsed[0].estimated_cost, "not published")
        self.assertEqual(parsed[0].source, "Browser Use live booking search")

    def test_appointment_payment_quote_and_unpaid_result(self):
        request = self.make_request("Find a dermatologist near Westwood")
        quote = build_appointment_payment_quote(request)
        unpaid = appointment_unpaid_result(request, "demo reject")

        self.assertEqual(quote.amount, APPOINTMENT_SERVICE_FEE_FET)
        self.assertEqual(quote.payment_method, "fet_direct")
        self.assertEqual(unpaid.status, "payment_required")

    def test_appointment_refreshes_old_payment_request_version(self):
        request = self.make_request(
            "Find a primary care doctor near USC Village",
            {"specialty": "primary care", "location": "USC Village", "urgency": "routine"},
        )
        quote = build_appointment_payment_quote(request)
        pending = PendingAppointmentPayment(
            original_sender="appointment-legacy-user",
            request=request,
            quote=quote,
            response_channel="chat",
            request_fingerprint=appointment_request_fingerprint(request),
            created_at=9999999999,
            request_version="appointment-fet-direct-v1",
        )

        self.assertNotEqual(pending.request_version, APPOINTMENT_PAYMENT_REQUEST_VERSION)
        self.assertTrue(appointment_pending_requires_refresh(pending, appointment_request_fingerprint(request)))

    def test_appointment_mri_intent_targets_imaging_center(self):
        text = "Can you find a doctor who can perform an MRI scan for my knee near USC Village right now?"

        self.assertTrue(is_appointment_intent(text))
        self.assertEqual(infer_appointment_specialty(text), "imaging center")
        self.assertEqual(infer_location(text), "USC Village")

        response = appointment_chat_response(None, "appointment-mri-user", text)

        self.assertIn("imaging center", response)
        self.assertIn("MRI note", response)
        self.assertIn("Service fee required", response)

    def test_appointment_result_is_concise_and_imaging_safe(self):
        request = self.make_request(
            "Find an MRI scan near USC Village",
            {"specialty": "imaging center", "location": "USC Village", "urgency": "routine"},
        )
        search = build_appointment_search_quote(request)
        preview = format_appointment_search_preview(search)

        self.assertIn("Real options", preview)
        self.assertIn("MRI/imaging note", preview)
        self.assertIn("Caregiver update", preview)
        self.assertLessEqual(preview.count("\n1."), 1)

    def test_appointment_followup_uses_existing_search_context(self):
        sender = "appointment-followup-user"
        APPOINTMENT_CONTEXT_BY_SENDER.pop(sender, None)
        request = self.make_request(
            "Find an MRI scan near USC Village",
            {"specialty": "imaging center", "location": "USC Village", "urgency": "routine"},
        )
        APPOINTMENT_CONTEXT_BY_SENDER[sender] = build_appointment_search_quote(request)

        link_only = appointment_chat_response(None, sender, "give me the link only")
        caregiver = appointment_chat_response(None, sender, "tell my daughter")

        self.assertIn("Booking/check link", link_only)
        self.assertNotIn("Service fee required", link_only)
        self.assertIn("Caregiver update", caregiver)

    def test_browser_cache_dedupes_equivalent_payloads(self):
        calls = {"count": 0}

        def loader():
            calls["count"] += 1
            return [{"value": "cached"}]

        with NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            cache_path = handle.name

        try:
            Path(cache_path).write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"CARELOOP_BROWSER_CACHE_PATH": cache_path}):
                import browser_cache

                browser_cache._memory_cache = None
                first, first_cached = cached_browser_call(
                    namespace="test",
                    payload={"Location": "USC Village", "Need": "Primary Care"},
                    loader=loader,
                    ttl_seconds=3600,
                )
                second, second_cached = cached_browser_call(
                    namespace="test",
                    payload={"Need": " primary   care ", "Location": "usc village"},
                    loader=loader,
                    ttl_seconds=3600,
                )

            self.assertFalse(first_cached)
            self.assertTrue(second_cached)
            self.assertEqual(first, second)
            self.assertEqual(calls["count"], 1)
            self.assertEqual(
                browser_cache_key("test", {"Location": "USC Village", "Need": "Primary Care"}),
                browser_cache_key("test", {"Need": " primary   care ", "Location": "usc village"}),
            )
        finally:
            Path(cache_path).unlink(missing_ok=True)

    def test_triage_blocks_emergency(self):
        result = triage_request(self.make_request("My dad has chest pain and cannot breathe"))

        self.assertEqual(result.status, "urgent_escalation")
        self.assertIn("Call emergency services now.", result.next_actions)
        self.assertEqual(triage_emergency_reason("face drooping and slurred speech"), "possible stroke symptoms")

    def test_triage_routes_specialists(self):
        cases = [
            ("Please explain this prescription label", "careloop-prescription-explainer"),
            ("Find the best allergy medicine near UCLA", "careloop-pharmacy-assistant"),
            ("Find an MRI scan near USC Village", "careloop-appointment-assistant"),
            ("Write a text to my daughter that Dad's appointment is booked", "careloop-caregiver-notifier"),
            ("Remind me to take metformin every morning", "careloop-adherence"),
            ("Is my prescription ready at CVS?", "careloop-orchestrator"),
        ]

        for text, route in cases:
            with self.subTest(text=text):
                self.assertEqual(triage_route(text)["route"], route)

    def test_triage_chat_is_stateful_for_short_followup(self):
        sender = "triage-followup-user"
        TRIAGE_CONTEXT_BY_SENDER.pop(sender, None)

        first = triage_chat_response(None, sender, "I need a doctor for knee pain")
        second = triage_chat_response(None, sender, "near USC with Medicare")

        self.assertIn("@careloop-appointment-assistant", first)
        self.assertIn("@careloop-appointment-assistant", second)
        self.assertIn(sender, TRIAGE_CONTEXT_BY_SENDER)

    def test_triage_chat_greeting_and_clarify(self):
        greeting = triage_chat_response(None, "triage-hi-user", "hi")
        unclear = triage_chat_response(None, "triage-unclear-user", "I am not sure")

        self.assertIn("CareLoop Triage", greeting)
        self.assertIn("I need one detail", unclear)

    def test_orchestrator_routes_paid_specialist_with_timeline(self):
        sender = "orchestrator-paid-user"
        ORCHESTRATOR_CONTEXT_BY_SENDER.pop(sender, None)

        response = orchestrator_chat_response(None, sender, "Find an MRI scan near USC Village")
        timeline = orchestrator_chat_response(None, sender, "timeline")

        self.assertIn("nearby appointment and imaging options", response)
        self.assertIn("0.1 FET", response)
        self.assertIn("clinician order or referral", response)
        self.assertIn("Payment requested", timeline)

    def test_orchestrator_handles_local_caregiver_flow(self):
        sender = "orchestrator-caregiver-user"
        ORCHESTRATOR_CONTEXT_BY_SENDER.pop(sender, None)

        response = orchestrator_chat_response(
            None,
            sender,
            "Write a text to my daughter that Dad's appointment is booked tomorrow",
        )

        self.assertIn("Here’s a caregiver message", response)
        self.assertIn("daughter", response)
        self.assertNotIn("CareLoop timeline", response)

    def test_orchestrator_blocks_emergency(self):
        response = orchestrator_chat_response(None, "orchestrator-emergency-user", "My dad has chest pain")

        self.assertIn("Call 911", response)
        self.assertNotIn("CareLoop timeline", response)

    def test_orchestrator_answers_followup_from_saved_appointment_search(self):
        sender = "orchestrator-followup-user"
        ORCHESTRATOR_CONTEXT_BY_SENDER.pop(sender, None)
        session = OrchestratorSession(case_id="careloop-test")
        ORCHESTRATOR_CONTEXT_BY_SENDER[sender] = session
        option = AppointmentOption(
            provider_name="USC Imaging Center",
            specialty="imaging center",
            location="1234 Jefferson Blvd, Los Angeles, CA",
            phone="213-555-0100",
            booking_url="https://example.com/book",
            source="test",
        )
        session.last_paid_route = "careloop-appointment-assistant"
        session.last_appointment_search = AppointmentSearchQuote(
            case_id=session.case_id,
            specialty="imaging center",
            location="USC Village",
            options=[option],
            selected_option=option,
            data_sources=["test"],
            status="booking_handoff_ready",
            payment_quote=PaymentQuote(
                case_id=session.case_id,
                service_name="CareLoop Appointment Search",
                amount="0.1",
                reference="test-ref",
            ),
        )

        response = orchestrator_chat_response(None, sender, "can you give me the closest location")

        self.assertIn("USC Imaging Center", response)
        self.assertIn("1234 Jefferson Blvd", response)
        self.assertNotIn("I need one detail", response)

    def test_orchestrator_caregiver_message_wins_over_booking_followup(self):
        sender = "orchestrator-caregiver-after-appointment-user"
        ORCHESTRATOR_CONTEXT_BY_SENDER.pop(sender, None)
        session = OrchestratorSession(case_id="careloop-test")
        ORCHESTRATOR_CONTEXT_BY_SENDER[sender] = session
        option = AppointmentOption(
            provider_name="UCLA Health",
            specialty="primary care",
            location="100 UCLA Medical Plaza, Los Angeles, CA",
            phone="310-555-0100",
            booking_url="https://example.com/ucla",
            source="test",
        )
        session.last_text = "i have a bad cough right now. can you find a doctor near UCLA main campus. i am nearby"
        session.last_paid_route = "careloop-appointment-assistant"
        session.last_appointment_search = AppointmentSearchQuote(
            case_id=session.case_id,
            specialty="primary care",
            location="UCLA",
            options=[option],
            selected_option=option,
            data_sources=["test"],
            status="booking_handoff_ready",
            payment_quote=PaymentQuote(
                case_id=session.case_id,
                service_name="CareLoop Appointment Search",
                amount="0.1",
                reference="test-ref",
            ),
        )

        response = orchestrator_chat_response(
            None,
            sender,
            "can you write a message to my daughter and let her know that i am booking an appointment",
        )

        self.assertIn("Here’s a caregiver message", response)
        self.assertIn("daughter", response)
        self.assertIn("UCLA Health", response)
        self.assertNotIn("Use this booking/search link", response)
        self.assertNotIn("bad cough", response)

    def test_prescription_and_orchestrator_outputs(self):
        request = self.make_request("Explain lisinopril 10mg and book a doctor")
        prescription = explain_prescription(request)
        orchestrated = orchestrate_care(request)

        self.assertEqual(prescription.status, "completed")
        self.assertIn("not medical advice", prescription.summary)
        self.assertEqual(orchestrated.agent_name, "careloop-orchestrator")
        self.assertIn("Prescription explained", orchestrated.timeline_events)

    def test_prescription_document_text_explains_actual_label_text(self):
        request = PrescriptionDocumentRequest(
            case_id="case-rx-doc-001",
            user_id="user-test",
            document_text=(
                "Rx Lisinopril 10 mg tablets. Sig: Take one tablet by mouth once daily. "
                "Qty: 30 Refills: 2 Prescriber: Dr. Maya Chen"
            ),
        )

        result = explain_prescription_document(request)

        self.assertEqual(result.status, "completed")
        self.assertIn("Lisinopril", result.summary)
        self.assertIn("once daily", result.summary)
        self.assertIn("not medical advice", result.summary)

    def test_prescription_document_handles_multiple_labels(self):
        request = PrescriptionDocumentRequest(
            case_id="case-rx-doc-002",
            user_id="user-test",
            document_text=(
                "Take 1 tablet by mouth, daily.\n"
                "Apixaban (Eliquis) .5 mg (Qty 30)\n"
                "Refills: 0\n"
                "Dr. Frank Hemorrhage\n"
                "Take 1 tablet by mouth, twice a day before meals\n"
                "Pantoprazole 20 mg (Qty 90)\n"
                "Refills: 1\n"
                "Dr. Carl Jr. Habanero"
            ),
        )

        result = explain_prescription_document(request)

        self.assertEqual(result.status, "completed")
        self.assertIn("Apixaban", result.summary)
        self.assertIn("Pantoprazole", result.summary)
        self.assertIn("twice a day before meals", result.summary)

    def test_prescription_document_handles_medicine_list_table_ocr(self):
        request = PrescriptionDocumentRequest(
            case_id="case-rx-doc-004",
            user_id="user-test",
            document_text=(
                "Prescription Medicine List\n"
                "Medication Dosage Frequency Route of Indications Refills\n"
                "Name Administration\n"
                "Amoxicillin S0Omg 3timesaday Oral Bacterial infections 2\n"
                "Metformin 500mg Twiceaday Oral Type 2 diabetes 3\n"
                "Lisinopril 20 mg Once daily Oral Hypertension 0\n"
                "Atorvastatin 10mg Once daily Oral Hyperlipidemia 1\n"
                "Albuterol 90mcg Asneeded Inhalation Asthma or COPD 3\n"
                "Inhaler exacerbation"
            ),
        )

        result = explain_prescription_document(request)

        self.assertEqual(result.status, "completed")
        self.assertIn("Amoxicillin 500 mg", result.summary)
        self.assertIn("3 times a day", result.summary)
        self.assertIn("Metformin 500 mg", result.summary)
        self.assertIn("twice a day", result.summary)
        self.assertIn("Albuterol 90 mcg", result.summary)

    def test_prescription_document_prefers_attachment_over_prompt_text(self):
        with NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("Rx Lisinopril 20 mg. Sig: Take one tablet by mouth once daily.")
            path = handle.name

        try:
            request = PrescriptionDocumentRequest(
                case_id="case-rx-doc-005",
                user_id="user-test",
                document_text="please explain this image",
                document_path=path,
            )

            result = explain_prescription_document(request)

            self.assertEqual(result.status, "completed")
            self.assertIn("Lisinopril 20 mg", result.summary)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_prescription_document_rejects_binary_garbage(self):
        request = PrescriptionDocumentRequest(
            case_id="case-rx-doc-003",
            user_id="user-test",
            document_text="\x00\x01ftypavif\x00\x00random bottles 3 G daily",
        )

        result = explain_prescription_document(request)

        self.assertEqual(result.status, "needs_clearer_document")
        self.assertNotIn("bottles", result.summary.lower())

    def test_prescription_chat_handles_greeting_without_fake_scan(self):
        response = prescription_chat_response(None, "user-test", "hi")

        self.assertIn("prescription", response.lower())
        self.assertNotIn("Medication not confidently detected", response)
        self.assertNotIn("Status:", response)

    def test_prescription_chat_asks_for_label_when_intent_is_unclear(self):
        response = prescription_chat_response(None, "user-test", "what is your favorite color?")

        self.assertIn("upload", response.lower())
        self.assertNotIn("Medication not confidently detected", response)

    def test_prescription_chat_answers_followup_from_last_scan(self):
        sender = "user-followup"
        PRESCRIPTION_CONTEXT_BY_SENDER.pop(sender, None)

        first_response = prescription_chat_response(
            None,
            sender,
            (
                "Prescription Medicine List\n"
                "Amoxicillin 500mg 3timesaday Oral Bacterial infections 2\n"
                "Metformin 500mg Twiceaday Oral Type 2 diabetes 3\n"
                "Albuterol 90mcg Asneeded Inhalation Asthma or COPD 3"
            ),
        )
        followup_response = prescription_chat_response(
            None,
            sender,
            "is there any order to take the medications",
        )

        self.assertIn("Amoxicillin", first_response)
        self.assertIn("Amoxicillin", followup_response)
        self.assertIn("Metformin", followup_response)
        self.assertIn("usually is not a single required order", followup_response)
        self.assertNotIn("upload", followup_response.lower())


if __name__ == "__main__":
    unittest.main()
