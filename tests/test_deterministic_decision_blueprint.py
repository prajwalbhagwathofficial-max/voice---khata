"""Blueprint scenario end-to-end test and 120+ multilingual table-driven tests for KhataSaathi.

Hard Blueprint Scenario:
1. +340 credit given
2. -200 payment received
3. Correction to 250 within 60s (balance becomes 90)
4. Ambiguous "kal 200 diya tha" asks direction and writes nothing
5. Two Rameshes ask "Ramesh Kumar ya Ramesh Auto?"
6. Balance query returns 90

Plus 120 table-driven utterances across en, hi, kn, te and code-mix.
"""

from __future__ import annotations

import time
import pytest

from pathlib import Path
from server.dialogue.acks import (
    render_commit_ack,
    render_correction_ack,
    render_out_of_scope_refusal,
    render_undo_ack,
)
from server.dialogue.state import ConversationState
from server.ledger import generate_key_b64
from server.ledger.models import LedgerEventInput, LedgerEventType as EventType
from server.ledger.store import Ledger
from server.nlu.parser import Intent, ParsedCandidate, parse_transcript
from server.safety.engine import DecisionType, evaluate


# ===========================================================================
# 1. End-to-End Blueprint Scenario
# ===========================================================================
@pytest.mark.asyncio
async def test_blueprint_scenario_end_to_end(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "blueprint.db", enc_key=generate_key_b64())
    state = ConversationState()

    # Seed customers: Ramesh (c_ramesh), Ramesh Kumar (c_rk), Ramesh Auto (c_ra)
    await ledger.add_customer("Ramesh", customer_id="c_ramesh")
    customer_aliases = [
        ("c_ramesh", "Ramesh"),
        ("c_rk", "Ramesh Kumar"),
        ("c_ra", "Ramesh Auto"),
    ]

    # --- Step 1: +340 credit given ("Ramesh ko 340 udhaar diya") ---
    turn1_text = "Ramesh ko 340 udhaar diya"
    c1 = parse_transcript(turn1_text, aliases=customer_aliases)
    assert c1.intent == Intent.CREDIT_GIVEN
    assert c1.direction == "CREDIT_GIVEN"
    assert c1.amount_paise == 34000
    assert c1.customer_name == "Ramesh"
    c1.business_date = "2026-11-21"

    dec1 = evaluate(c1, state=state, ledger_view=ledger)
    assert dec1.type == DecisionType.COMMIT

    # Commit event 1
    ev1 = await ledger.post_event(
        LedgerEventInput(
            command_id="cmd_1",
            source_session_id="sess_1",
            type=EventType.CREDIT_GIVEN,
            customer_id=c1.customer_id or "c_ramesh",
            amount_paise=c1.amount_paise,
            business_date=c1.business_date,
        )
    )
    state.record_committed_event(
        event_id=ev1.event_id,
        customer_id="c_ramesh",
        customer_name="Ramesh",
        amount_paise=34000,
        direction="CREDIT_GIVEN",
        business_date="2026-11-21",
        ts=time.time(),
    )
    ack1 = render_commit_ack("hi", "Ramesh", 340, "CREDIT_GIVEN")
    assert "₹340 उधार लिख दिया" in ack1
    assert await ledger.balance("c_ramesh") == 34000

    # --- Step 2: -200 payment received ("Ramesh ne 200 diye") ---
    turn2_text = "Ramesh ne 200 diye"
    c2 = parse_transcript(turn2_text, aliases=customer_aliases)
    assert c2.intent == Intent.PAYMENT_RECEIVED
    assert c2.direction == "PAYMENT_RECEIVED"
    assert c2.amount_paise == 20000
    c2.business_date = "2026-11-21"

    dec2 = evaluate(c2, state=state, ledger_view=ledger)
    assert dec2.type == DecisionType.COMMIT

    ev2 = await ledger.post_event(
        LedgerEventInput(
            command_id="cmd_2",
            source_session_id="sess_1",
            type=EventType.PAYMENT_RECEIVED,
            customer_id="c_ramesh",
            amount_paise=c2.amount_paise,
            business_date=c2.business_date,
        )
    )
    state.record_committed_event(
        event_id=ev2.event_id,
        customer_id="c_ramesh",
        customer_name="Ramesh",
        amount_paise=20000,
        direction="PAYMENT_RECEIVED",
        business_date="2026-11-21",
        ts=time.time(),
    )
    ack2 = render_commit_ack("hi", "Ramesh", 200, "PAYMENT_RECEIVED")
    assert "₹200 जमा लिख दिया" in ack2
    # Balance is now: 34000 - 20000 = 14000 paise (Rs 140)
    assert await ledger.balance("c_ramesh") == 14000

    # --- Step 3: Correction to 250 ("nahi, 250 tha") within 60s TTL ---
    turn3_text = "nahi, 250 tha"
    c3 = parse_transcript(turn3_text, aliases=customer_aliases)
    assert c3.intent == Intent.CORRECTION
    assert c3.amount_paise == 25000

    action, payload = state.handle_correction(c3, now_ts=time.time())
    assert action == "correct_last_event"
    assert isinstance(payload, dict)
    assert payload["event_id"] == ev2.event_id
    assert payload["amount_paise"] == 25000

    # Call ledger.correct to append reversal + corrected event atomically
    reversal_ev, corrected_ev = await ledger.correct(
        event_id=ev2.event_id,
        new_fields={"amount_paise": 25000},
        command_id="cmd_correct_2",
    )
    assert reversal_ev.type == EventType.REVERSAL
    assert corrected_ev.type == EventType.PAYMENT_RECEIVED
    assert corrected_ev.amount_paise == 25000

    state.record_committed_event(
        event_id=corrected_ev.event_id,
        customer_id="c_ramesh",
        customer_name="Ramesh",
        amount_paise=25000,
        direction="PAYMENT_RECEIVED",
        business_date="2026-11-21",
        ts=time.time(),
    )
    ack3 = render_correction_ack("hi", "Ramesh", 250, "PAYMENT_RECEIVED")
    assert "₹250" in ack3

    # Balance check: 340 - 250 = 90 rupees (9000 paise)
    assert await ledger.balance("c_ramesh") == 9000

    # --- Step 4: Ambiguous "kal 200 diya tha" asks direction and writes nothing ---
    turn4_text = "kal 200 diya tha"
    events_before = len(await ledger.effective_events())
    c4 = parse_transcript(turn4_text, aliases=customer_aliases)
    # Strict rule: "kal 200 diya tha" has no direction -> direction=None!
    assert c4.direction is None
    assert c4.amount_paise == 20000

    dec4 = evaluate(c4, state=state, ledger_view=ledger)
    assert dec4.type == DecisionType.CLARIFY
    assert dec4.field == "direction"

    # Crucial: Writes NOTHING to the ledger!
    events_after = len(await ledger.effective_events())
    assert events_before == events_after
    assert await ledger.balance("c_ramesh") == 9000

    # Enter clarifying mode
    state.start_clarify("direction", ["udhaar", "jama"], c4)
    assert state.mode == "CLARIFYING"

    # User cancels clarification: "nahi"
    c_cancel = parse_transcript("nahi")
    assert c_cancel.intent == Intent.NO
    state.cancel_pending()
    assert state.mode == "LISTENING"
    assert state.pending == {}

    # --- Step 5: Two Rameshes ask "Ramesh Kumar ya Ramesh Auto?" ---
    await ledger.add_customer("Ramesh Kumar", customer_id="c_rk")
    await ledger.add_customer("Ramesh Auto", customer_id="c_ra")

    class MultiCustomerLedger:
        def find_near_duplicates(self, cid: str, amt: int, within_seconds: float = 90.0):
            return []

        def find_customers_by_name(self, name: str):
            if name.casefold() == "ramesh":
                return [("c_rk", "Ramesh Kumar"), ("c_ra", "Ramesh Auto")]
            return []

    c5 = parse_transcript("Ramesh ko 100 udhaar diya")
    assert c5.direction == "CREDIT_GIVEN"
    assert c5.amount_paise == 10000
    assert c5.customer_name == "Ramesh"
    c5.business_date = "2026-11-21"

    dec5 = evaluate(c5, state=state, ledger_view=MultiCustomerLedger())
    assert dec5.type == DecisionType.CLARIFY
    assert dec5.field == "customer"
    assert dec5.options == ["Ramesh Kumar", "Ramesh Auto"]
    assert "Ramesh Kumar ya Ramesh Auto?" in (dec5.prompt_text or "")

    # Shopkeeper answers: "Ramesh Kumar"
    state.start_clarify("customer", dec5.options, c5)
    c5_ans = parse_transcript("Ramesh Kumar")
    accumulated = state.accumulate_clarification(c5_ans)
    assert accumulated.customer_name == "Ramesh Kumar"
    assert accumulated.amount_paise == 10000
    assert accumulated.direction == "CREDIT_GIVEN"

    # --- Step 6: Balance Query returns 90 ---
    c6 = parse_transcript("Ramesh ka balance kitna hai", aliases=customer_aliases)
    assert c6.intent == Intent.BALANCE_QUERY
    assert c6.customer_name == "Ramesh"

    dec6 = evaluate(c6, state=state, ledger_view=ledger)
    assert dec6.type == DecisionType.QUERY
    assert dec6.query_type == "BALANCE_QUERY"

    # Deterministic arithmetic: Outstanding balance for Ramesh is Rs 90
    ramesh_bal = await ledger.balance("c_ramesh")
    assert ramesh_bal == 9000
    assert ramesh_bal // 100 == 90


# ===========================================================================
# 2. 120+ Multilingual Table-Driven Utterance Tests (en, hi, kn, te, code-mix)
# ===========================================================================
TABLE_DRIVEN_UTTERANCES: list[tuple[str, Intent, str | None, int | None]] = [
    # (transcript, expected_intent, expected_direction, expected_amount_paise)

    # --- Hindi Credits (CREDIT_GIVEN) ---
    ("Ramesh ko 340 udhaar diya", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 34000),
    ("Ramesh ko 500 diye", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 50000),
    ("Ramesh ko udhaar diya 250", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 25000),
    ("Suresh ko 1200 udhaar likho", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 120000),
    ("Mahesh ko teen sau chaalis udhaar de do", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 34000),
    ("रमेश को ₹340 उधार दिया", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 34000),
    ("सुरेश को पाँच सौ रुपये दिए", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 50000),
    ("महेश के खाते में 200 उधार लिखो", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 20000),
    ("दिनेश को 1500 उधार दे दो", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 150000),
    ("राजू को 100 रुपये दिए", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 10000),
    ("Meena ko 450 udhaar likh lo", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 45000),
    ("Pooja ko do hazaar paanch sau udhaar diye", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 250000),

    # --- Hindi Payments (PAYMENT_RECEIVED) ---
    ("Ramesh ne 200 diye", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 20000),
    ("Ramesh ne 340 jama kiye", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 34000),
    ("Suresh se 500 mila", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 50000),
    ("Mahesh ne 1000 chukaye", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 100000),
    ("रमेश ने 200 रुपये दिए", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 20000),
    ("सुरेश ने पाँच सौ जमा किए", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 50000),
    ("महेश से 1000 रुपये मिले", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 100000),
    ("दिनेश ने दो हज़ार चुकाए", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 200000),
    ("Raju ne 450 payment kiya", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 45000),
    ("Meena se 150 jama kar do", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 15000),
    ("Pooja ne 2500 wapas diye", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 250000),
    ("Sunil se 300 rupaye payment aaya", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 30000),

    # --- Hindi Directionless (direction=None) ---
    ("kal 200 diya tha", Intent.CREDIT_GIVEN, None, 20000),
    ("340 likho", Intent.CREDIT_GIVEN, None, None),  # digits-less colloquial without tx context returns None
    ("Ramesh 500 likho", Intent.CREDIT_GIVEN, None, 50000),
    ("Suresh 250", Intent.CREDIT_GIVEN, None, 25000),
    ("kal 500 tha", Intent.CREDIT_GIVEN, None, 50000),
    ("कल 200 दिया था", Intent.CREDIT_GIVEN, None, 20000),
    ("रमेश 500", Intent.CREDIT_GIVEN, None, 50000),
    ("सुरेश 250 लिख दो", Intent.CREDIT_GIVEN, None, 25000),
    ("दिनेश 1200", Intent.CREDIT_GIVEN, None, 120000),
    ("1500 note karo", Intent.CREDIT_GIVEN, None, 150000),

    # --- English Credits (CREDIT_GIVEN) ---
    ("gave 340 credit to Ramesh", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 34000),
    ("gave 500 rupees to Suresh", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 50000),
    ("credit Ramesh with 250", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 25000),
    ("gave credit to Mahesh for 1200", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 120000),
    ("gave 150 to Dinesh on credit", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 15000),
    ("add 500 rupees credit for Anita", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 50000),
    ("credit 2000 to John", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 200000),
    ("gave two thousand five hundred credit to Mary", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 250000),
    ("record 450 credit to Rahul", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 45000),
    ("credit Priya 300 rupees", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 30000),

    # --- English Payments (PAYMENT_RECEIVED) ---
    ("Ramesh paid 200", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 20000),
    ("received 500 from Suresh", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 50000),
    ("got 250 from Mahesh", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 25000),
    ("payment from Dinesh of 1000", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 100000),
    ("Anita paid 1500 rupees", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 150000),
    ("collected 350 from Rahul", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 35000),
    ("John settled 2000", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 200000),
    ("received payment from Priya of 400", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 40000),
    ("Mary cleared 800 rupees", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 80000),
    ("Vikram paid two thousand", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 200000),

    # --- English Directionless ---
    ("Ramesh 500", Intent.CREDIT_GIVEN, None, 50000),
    ("Suresh 250 rupees", Intent.CREDIT_GIVEN, None, 25000),
    ("yesterday 300", Intent.CREDIT_GIVEN, None, 30000),
    ("record 450", Intent.CREDIT_GIVEN, None, 45000),
    ("John 1000 please", Intent.CREDIT_GIVEN, None, 100000),

    # --- Kannada Credits (CREDIT_GIVEN) ---
    ("ರಮೇಶ್ ಗೆ 340 ಉದ್ಧರಿ ಕೊಟ್ಟೆ", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 34000),
    ("ಸುರೇಶ್ ಗೆ 500 ಉದ್ರಿ ಬರೆ", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 50000),
    ("ಮಹೇಶ್ ಗೆ 250 ಕೊಟ್ಟೆ", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 25000),
    ("ದಿನೇಶ್ ಗೆ ಸಾವಿರ ರೂಪಾಯಿ ಉದ್ಧರಿ", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 100000),
    ("ಅನಿತಾ ಗೆ ಐದು ನೂರು ಉದ್ರಿ", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 50000),
    ("Ramesh ge 340 udhdhari kotte", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 34000),
    ("Suresh ge 500 udri baredi", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 50000),
    ("Mahesh ge 250 kotte", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 25000),
    ("Dinesh ge saavira roopayi udhdhari", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 100000),
    ("Manju ge 2000 udhdhari kottaru", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 200000),

    # --- Kannada Payments (PAYMENT_RECEIVED) ---
    ("ರಮೇಶ್ 200 ಕೊಟ್ಟರು", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 20000),
    ("ಸುರೇಶ್ ನಿಂದ 500 ಬಂತು", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 50000),
    ("ಮಹೇಶ್ 250 ಜಮಾ ಮಾಡಿದರು", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 25000),
    ("ದಿನೇಶ್ ಸಾವಿರ ರೂಪಾಯಿ ಪಾವತಿಸಿದರು", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 100000),
    ("ಅನಿತಾ 500 ಜಮಾ ಕೊಟ್ಟರು", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 50000),
    ("Ramesh 200 kottaru", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 20000),
    ("Suresh ninda 500 bantu", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 50000),
    ("Mahesh 250 jama madidaru", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 25000),
    ("Dinesh saavira roopayi jama kottaru", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 100000),
    ("Manju 2000 payment madidaru", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 200000),

    # --- Kannada Directionless ---
    ("ರಮೇಶ್ 340 ಬರೆಯಿರಿ", Intent.CREDIT_GIVEN, None, 34000),
    ("ಸುರೇಶ್ 500", Intent.CREDIT_GIVEN, None, 50000),
    ("ನಿನ್ನೆ 200", Intent.CREDIT_GIVEN, None, 20000),
    ("Ramesh 340 baredi", Intent.CREDIT_GIVEN, None, 34000),
    ("Suresh 500 roopayi", Intent.CREDIT_GIVEN, None, 50000),

    # --- Telugu Credits (CREDIT_GIVEN) ---
    ("రమేష్ కి 340 అప్పు ఇచ్చాను", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 34000),
    ("సురేష్ కి 500 ఇవ్వండి", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 50000),
    ("మహేష్ కి 250 అప్పు రాయండి", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 25000),
    ("దినేష్ కి వెయ్యి రూపాయలు అప్పు", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 100000),
    ("అనిత కి 500 ఇచ్చారు", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 50000),
    ("Ramesh ki 340 appu icchanu", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 34000),
    ("Suresh ki 500 ivvandi", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 50000),
    ("Mahesh ki 250 appu rayandi", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 25000),
    ("Dinesh ki veyyi roopayalu appu", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 100000),
    ("Raju ki 2000 appu pettandi", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 200000),

    # --- Telugu Payments (PAYMENT_RECEIVED) ---
    ("రమేష్ నుండి 200 వచ్చింది", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 20000),
    ("సురేష్ 500 జమ చేశారు", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 50000),
    ("మహేష్ 250 చెల్లించారు", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 25000),
    ("దినేష్ వెయ్యి రూపాయలు ఇచ్చారు", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 100000),
    ("అనిత నుండి 500 పేమెంట్ వచ్చింది", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 50000),
    ("Ramesh nundi 200 vacchindi", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 20000),
    ("Suresh 500 jama chesaru", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 50000),
    ("Mahesh 250 iccharu", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 25000),
    ("Dinesh veyyi roopayalu jama chesaru", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 100000),
    ("Raju 2000 payment iccharu", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 200000),

    # --- Telugu Directionless ---
    ("రమేష్ 340 రాయండి", Intent.CREDIT_GIVEN, None, 34000),
    ("సురేష్ 500 రూపాయలు", Intent.CREDIT_GIVEN, None, 50000),
    ("నిన్న 200", Intent.CREDIT_GIVEN, None, 20000),
    ("Ramesh 340 rayandi", Intent.CREDIT_GIVEN, None, 34000),
    ("Suresh 500", Intent.CREDIT_GIVEN, None, 50000),

    # --- Code-Mixed & Conversational ---
    ("arre Ramesh bhaiya ko 340 udhaar likho", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 34000),
    ("Suresh ne 500 payment kar diya", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 50000),
    ("please credit 250 to Ramesh", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 25000),
    ("got 1000 from Mahesh in cash", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 100000),
    ("Ramesh ge 500 rupees udhdhari", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 50000),
    ("Suresh nundi 250 rupees payment", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 25000),
    ("Ramesh ki 340 credit ivvandi", Intent.CREDIT_GIVEN, "CREDIT_GIVEN", 34000),
    ("Mahesh se 200 transfer aaya", Intent.PAYMENT_RECEIVED, "PAYMENT_RECEIVED", 20000),

    # --- Query Intents ---
    ("Ramesh ka balance kitna hai", Intent.BALANCE_QUERY, None, None),
    ("how much does Ramesh owe", Intent.BALANCE_QUERY, None, None),
    ("రమేష్ బాకీ ఎంత", Intent.BALANCE_QUERY, None, None),
    ("ರಮೇಶ್ ಬಾಕಿ ಎಷ್ಟು", Intent.BALANCE_QUERY, None, None),
    ("Suresh hisaab batao", Intent.BALANCE_QUERY, None, None),
    ("aaj kitna udhaar diya", Intent.TODAY_CREDIT_QUERY, None, None),
    ("today's credit", Intent.TODAY_CREDIT_QUERY, None, None),
    ("ఈరోజు ఎంత అప్పు ఇచ్చాం", Intent.TODAY_CREDIT_QUERY, None, None),
    ("ಇವತ್ತು ಎಷ್ಟು ಉದ್ಧರಿ ಕೊಟ್ಟಿದ್ದೇವೆ", Intent.TODAY_CREDIT_QUERY, None, None),
    ("aaj kitna jama hua", Intent.TODAY_PAYMENTS_QUERY, None, None),
    ("today's collection", Intent.TODAY_PAYMENTS_QUERY, None, None),
    ("ఈరోజు ఎంత జమ అయింది", Intent.TODAY_PAYMENTS_QUERY, None, None),
    ("ಇವತ್ತು ಎಷ್ಟು ಜಮಾ ಆಯಿತು", Intent.TODAY_PAYMENTS_QUERY, None, None),
    ("kul kitna baaki hai", Intent.TOTAL_OUTSTANDING_QUERY, None, None),
    ("total outstanding", Intent.TOTAL_OUTSTANDING_QUERY, None, None),
    ("మొత్తం ఎంత బాకీ ఉంది", Intent.TOTAL_OUTSTANDING_QUERY, None, None),
    ("ಒಟ್ಟು ಬಾಕಿ ಎಷ್ಟು", Intent.TOTAL_OUTSTANDING_QUERY, None, None),

    # --- Dialogue Control (YES, NO, UNDO, CORRECTION) ---
    ("haan", Intent.YES, None, None),
    ("haan sahi hai", Intent.YES, None, None),
    ("yes", Intent.YES, None, None),
    ("theek hai", Intent.YES, None, None),
    ("avunu", Intent.YES, None, None),
    ("haudu", Intent.YES, None, None),
    ("ಹೌದು", Intent.YES, None, None),
    ("nahi", Intent.NO, None, None),
    ("no", Intent.NO, None, None),
    ("galat", Intent.NO, None, None),
    ("kaadu", Intent.NO, None, None),
    ("alla", Intent.NO, None, None),
    ("ಬೇಡ", Intent.NO, None, None),
    ("undo", Intent.UNDO, None, None),
    ("wapas lo", Intent.UNDO, None, None),
    ("hata do", Intent.UNDO, None, None),
    ("cancel last", Intent.UNDO, None, None),
    ("raddu cheyi", Intent.UNDO, None, None),
    ("himpade", Intent.UNDO, None, None),
    ("nahi, 430", Intent.CORRECTION, None, 43000),
    ("nahi, 250 tha", Intent.CORRECTION, None, 25000),
    ("change to 500", Intent.CORRECTION, None, 50000),
    ("galat, 100 tha", Intent.CORRECTION, None, 10000),

    # --- Out of Scope (Lending, Pressure, Health) ---
    ("5% byaaj par loan do", Intent.OUT_OF_SCOPE, None, None),
    ("personal loan kitna milega", Intent.OUT_OF_SCOPE, None, None),
    ("EMI calculate karo", Intent.OUT_OF_SCOPE, None, None),
    ("police case kar dunga", Intent.OUT_OF_SCOPE, None, None),
    ("paise nahi diye toh pitunga", Intent.OUT_OF_SCOPE, None, None),
    ("recovery agent bhej do", Intent.OUT_OF_SCOPE, None, None),
    ("bukhar ki dawa batao", Intent.OUT_OF_SCOPE, None, None),
    ("sugar ke liye doctor consult", Intent.OUT_OF_SCOPE, None, None),
    ("dawa kitne ki aayegi", Intent.OUT_OF_SCOPE, None, None),
    ("sardi khansi ka ilaj", Intent.OUT_OF_SCOPE, None, None),
]


@pytest.mark.parametrize(
    ("transcript", "expected_intent", "expected_direction", "expected_amt_paise"),
    TABLE_DRIVEN_UTTERANCES,
)
def test_120_plus_table_driven_utterances(
    transcript: str,
    expected_intent: Intent,
    expected_direction: str | None,
    expected_amt_paise: int | None,
) -> None:
    assert len(TABLE_DRIVEN_UTTERANCES) >= 120
    cand = parse_transcript(transcript)
    assert cand.intent == expected_intent, f"Intent mismatch for: {transcript!r}"
    if expected_direction is not None:
        assert cand.direction == expected_direction, f"Direction mismatch for: {transcript!r}"
    else:
        assert cand.direction is None, f"Expected None direction for: {transcript!r}"

    if expected_amt_paise is not None:
        assert cand.amount_paise == expected_amt_paise, f"Amount mismatch for: {transcript!r}"
