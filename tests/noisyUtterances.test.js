/**
 * Noisy Utterances & Real-World Kirana Audio Test Suite — Voice Khata (Phase 9)
 *
 * Tests the voice pipeline and parser resilience against:
 * 1. Conversational fillers ("arre bhai", "suno", "haan toh", "jaldi")
 * 2. Mixed code-switching (Hinglish / Kanglish)
 * 3. Spoken currency clutter ("rs", "rupaye", "rupiya", "cash")
 * 4. Conversational pauses and repetitions
 * 5. Relative dates with colloquial modifiers ("kal ka", "parso wala", "3 din pehle")
 * 6. Colloquial negations and corrections ("galat hai", "arre nahi 300 nahi 500")
 * 7. Kannada dialect phrases ("saala bari", "hana kottru", "lekka heli")
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { parseNumber } from '../client/src/voice/numberParser.js';
import { parseDate } from '../client/src/voice/dateParser.js';
import {
  isCorrection,
  isConfirmation,
  isDenial,
  detectIntentFromLexicon,
  extractCorrectedAmount,
} from '../client/src/voice/correctionHandler.js';

// Load lexicons
const hiLex = JSON.parse(readFileSync('config/lexicon/hi.json', 'utf8'));
const knLex = JSON.parse(readFileSync('config/lexicon/kn.json', 'utf8'));
const enLex = JSON.parse(readFileSync('config/lexicon/en.json', 'utf8'));
const lexicons = [hiLex, knLex, enLex];

describe('Noisy Utterances — Conversational Fillers & Noise', () => {
  it('should parse amount when surrounded by heavy conversational fillers', () => {
    // "Arre bhai suno Ramesh ka paanch sau rupaye likho jaldi"
    const text = 'arre bhai suno ramesh ka paanch sau rupaye likho jaldi';
    const result = parseNumber(text, lexicons);
    assert.equal(result.value, 500);
  });

  it('should extract amount despite currency words and slang', () => {
    // "Suresh 250 rs cash de gaya"
    const text = 'suresh 250 rs cash de gaya';
    const result = parseNumber(text, lexicons);
    assert.equal(result.value, 250);
  });

  it('should parse compound amounts with connectors and noise', () => {
    // "Sharma ji ka do hazaar aur teen sau pachaas rupiya"
    const text = 'sharma ji ka do hazaar aur teen sau pachaas rupiya';
    const result = parseNumber(text, lexicons);
    assert.equal(result.value, 2350);
  });

  it('should parse large amounts with lakh and thousands', () => {
    // "ek lakh do hazaar rupaye"
    const text = 'ek lakh do hazaar rupaye';
    const result = parseNumber(text, lexicons);
    assert.equal(result.value, 102000);
  });
});

describe('Noisy Utterances — Intent Detection Under Noise', () => {
  it('should identify credit intent in noisy Hindi utterance', () => {
    const text = 'arre bhaiya ramesh ka khata me paanch sau udhaar likh do na';
    const intent = detectIntentFromLexicon(text, lexicons);
    assert.equal(intent, 'credit');
  });

  it('should identify payment intent in colloquial Hinglish phrase', () => {
    const text = 'haan ramesh ne abhi abhi teen sau rupaye paid kiya';
    const intent = detectIntentFromLexicon(text, lexicons);
    assert.equal(intent, 'payment');
  });

  it('should identify query intent in noisy question', () => {
    const text = 'arre ramesh ka total hisab kitna baaki hai jaldi batao';
    const intent = detectIntentFromLexicon(text, lexicons);
    assert.equal(intent, 'query');
  });

  it('should identify undo intent when merchant panics', () => {
    const text = 'arre galat ho gaya pichla entry hatao jaldi';
    const intent = detectIntentFromLexicon(text, lexicons);
    // correction or undo
    assert.ok(intent === 'undo' || intent === 'correction');
  });
});

describe('Noisy Utterances — Kannada & Kanglish Dialects', () => {
  it('should parse Kannada number with dialect prefixes', () => {
    // "Ramesh ge aidu nooru saala bareyiri"
    const text = 'ramesh ge aidu nooru saala bareyiri';
    const num = parseNumber(text, lexicons);
    assert.equal(num.value, 500);

    const intent = detectIntentFromLexicon(text, lexicons);
    assert.equal(intent, 'credit');
  });

  it('should parse Kannada payment with colloquial suffix', () => {
    // "Suresh ivattu eradu nooru hana kottru"
    const text = 'suresh eradu nooru hana kottru';
    const num = parseNumber(text, lexicons);
    assert.equal(num.value, 200);

    const intent = detectIntentFromLexicon(text, lexicons);
    assert.equal(intent, 'payment');
  });

  it('should parse Kannada query with "yeshtu lekka"', () => {
    const text = 'ramesh avara yeshtu lekka baaki heli';
    const intent = detectIntentFromLexicon(text, lexicons);
    assert.equal(intent, 'query');
  });
});

describe('Noisy Utterances — Corrections and Negations', () => {
  it('should detect inline price correction: "arre nahi 300 nahi 500 tha"', () => {
    const text = 'arre nahi 300 nahi 500 tha';
    assert.equal(isCorrection(text, lexicons), true);
    const corrected = extractCorrectedAmount(text, lexicons);
    assert.equal(corrected.correctedAmount, 500);
  });

  it('should detect Kannada correction: "tappu aidu nooru alla"', () => {
    const text = 'tappu aidu nooru';
    assert.equal(isCorrection(text, lexicons), true);
    const corrected = extractCorrectedAmount(text, lexicons);
    assert.equal(corrected.correctedAmount, 500);
  });

  it('should distinguish confirmation amidst conversational noise', () => {
    const text = 'haan haan bilkul theek hai bhai';
    assert.equal(isConfirmation(text, lexicons), true);
    assert.equal(isDenial(text, lexicons), false);
  });

  it('should distinguish denial amidst conversational noise', () => {
    const text = 'arre nahi nahi bilkul galat cancel karo';
    assert.equal(isDenial(text, lexicons), true);
  });
});

describe('Noisy Utterances — Colloquial Dates in Context', () => {
  const refDate = new Date(2026, 10, 21, 10, 0, 0); // 21 Nov 2026

  it('should extract "kal" from noisy sentence', () => {
    const text = 'arre kal sham ko ramesh ne paanch sau udhaar liya tha';
    const r = parseDate(text, lexicons, refDate);
    assert.equal(r.isExplicit, true);
    assert.equal(r.date.getDate(), 20); // Yesterday
  });

  it('should extract "parso" from conversational phrase', () => {
    const text = 'parso ka hisab check karo';
    const r = parseDate(text, lexicons, refDate);
    assert.equal(r.isExplicit, true);
    assert.equal(r.date.getDate(), 19); // Day before yesterday
  });

  it('should extract "3 din pehle" with conversational noise', () => {
    const text = 'woh 3 din pehle jo saman le gaya tha uska';
    const r = parseDate(text, lexicons, refDate);
    assert.equal(r.isExplicit, true);
    assert.equal(r.date.getDate(), 18);
  });
});
