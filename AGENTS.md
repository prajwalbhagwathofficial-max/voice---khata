Project: KhataSaathi, a voice-first, mistake-resistant ledger for kirana
stores (hackathon PS2). Product thesis: speech recognition is a component;
trustworthy transaction execution is the product.
ARCHITECTURE RULE: models propose meaning; deterministic code owns money,
identity checks, commits and balance arithmetic. No model-generated text
or SQL ever writes to the ledger. Never silently guess a financial field.
Clarification is a successful outcome.
HARD RULES: WebSocket-only dynamic data; no telephony, no phone numbers
stored; STT Gnani Prisma v3.0, TTS Gnani Timbre v3.0 (stock voices only,
no cloning); custom asyncio orchestration; keys only in .env; AI disclosure
on the first turn in the user's language; no lending, no pressure language,
no health advice; synthetic or consented data only; no raw audio retention.
MONEY: integer paise only, INR only, no floats. Positive balance = customer
owes the shop. Outstanding = SUM(credit_given) - SUM(payment_received).
LEDGER: append-only. Corrections append a REVERSAL plus a new event.
Never mutate or delete rows (except wipe_all).
SAFE REPLIES: never say "saved" before the DB commit succeeds.
LOGGING: never log customer names, amounts or transcripts in plaintext.
NON-GOALS: inventory, GST, lending, UPI initiation, customer app.
FEATURE FLAGS (default off): FEATURE_BILLS, FEATURE_SHARE.
Tests for every logic module. No features that are not requested.
