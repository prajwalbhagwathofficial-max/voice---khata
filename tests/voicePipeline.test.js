/**
 * Phase 6 Tests — Voice Pipeline
 *
 * Tests for:
 * - Number parser (Hindi, Kannada, English, mixed)
 * - Date parser (relative dates, explicit dates)
 * - Correction handler (negation, confirmation, intent detection)
 * - Disclosure manager (first-turn only)
 * - Barge-in controller (energy-based detection)
 * - Intent extractor module loading
 * - Voice pipeline orchestrator (processTranscript)
 */

import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Load lexicons
const hiLex = JSON.parse(readFileSync('config/lexicon/hi.json', 'utf8'));
const knLex = JSON.parse(readFileSync('config/lexicon/kn.json', 'utf8'));
const enLex = JSON.parse(readFileSync('config/lexicon/en.json', 'utf8'));
const lexicons = [hiLex, knLex, enLex];

// Load templates
const hiTpl = JSON.parse(readFileSync('config/templates/hi.json', 'utf8'));
const templates = { hi: hiTpl };

// ─── Number Parser ──────────────────────────────────────────

import { parseNumber } from '../client/src/voice/numberParser.js';

describe('Number Parser', () => {
  it('should parse Hindi word "paanch sau" → 500', () => {
    const r = parseNumber('paanch sau', lexicons);
    assert.equal(r.value, 500);
  });

  it('should parse Hindi "do hazaar" → 2000', () => {
    const r = parseNumber('do hazaar', lexicons);
    assert.equal(r.value, 2000);
  });

  it('should parse Hindi "do hazaar teen sau pachaas" → 2350', () => {
    const r = parseNumber('do hazaar teen sau pachaas', lexicons);
    assert.equal(r.value, 2350);
  });

  it('should parse Devanagari "पाँच सौ" → 500', () => {
    const r = parseNumber('पाँच सौ', lexicons);
    assert.equal(r.value, 500);
  });

  it('should parse Devanagari "दो हज़ार" → 2000', () => {
    const r = parseNumber('दो हज़ार', lexicons);
    assert.equal(r.value, 2000);
  });

  it('should parse English "five hundred" → 500', () => {
    const r = parseNumber('five hundred', lexicons);
    assert.equal(r.value, 500);
  });

  it('should parse English "two thousand three hundred fifty" → 2350', () => {
    const r = parseNumber('two thousand three hundred fifty', lexicons);
    assert.equal(r.value, 2350);
  });

  it('should parse Kannada "ಐದು ನೂರು" → 500', () => {
    const r = parseNumber('ಐದು ನೂರು', lexicons);
    assert.equal(r.value, 500);
  });

  it('should parse Kannada romanised "aidu nooru" → 500', () => {
    const r = parseNumber('aidu nooru', lexicons);
    assert.equal(r.value, 500);
  });

  it('should parse digit literal "250" → 250', () => {
    const r = parseNumber('250 rupees', lexicons);
    assert.equal(r.value, 250);
  });

  it('should parse digit literal "1,500" → 1500', () => {
    const r = parseNumber('1,500', lexicons);
    assert.equal(r.value, 1500);
  });

  it('should parse "sau" alone → 100', () => {
    const r = parseNumber('sau', lexicons);
    assert.equal(r.value, 100);
  });

  it('should parse "hazaar" alone → 1000', () => {
    const r = parseNumber('hazaar', lexicons);
    assert.equal(r.value, 1000);
  });

  it('should handle mixed "ek hazaar paanch sau" → 1500', () => {
    const r = parseNumber('ek hazaar paanch sau', lexicons);
    assert.equal(r.value, 1500);
  });

  it('should return null for non-number text', () => {
    const r = parseNumber('Ramesh ka hisab bata', lexicons);
    assert.equal(r.value, null);
  });

  it('should return null for empty input', () => {
    const r = parseNumber('', lexicons);
    assert.equal(r.value, null);
  });

  it('should handle connectors "paanch sau aur pachaas" → 550', () => {
    const r = parseNumber('paanch sau aur pachaas', lexicons);
    assert.equal(r.value, 550);
  });
});

// ─── Date Parser ────────────────────────────────────────────

import { parseDate } from '../client/src/voice/dateParser.js';

describe('Date Parser', () => {
  const now = new Date(2026, 10, 21, 12, 0, 0); // Nov 21, 2026

  it('should parse Hindi "aaj" → today', () => {
    const r = parseDate('aaj ka hisab', lexicons, now);
    assert.equal(r.date.getDate(), 21);
    assert.equal(r.isExplicit, true);
    assert.equal(r.matched, 'aaj');
  });

  it('should parse Hindi "kal" → yesterday', () => {
    const r = parseDate('kal ka udhar', lexicons, now);
    assert.equal(r.date.getDate(), 20);
    assert.equal(r.isExplicit, true);
  });

  it('should parse Hindi "parso" → 2 days ago', () => {
    const r = parseDate('parso wala', lexicons, now);
    assert.equal(r.date.getDate(), 19);
    assert.equal(r.isExplicit, true);
  });

  it('should parse English "yesterday" → yesterday', () => {
    const r = parseDate('yesterday payment', lexicons, now);
    assert.equal(r.date.getDate(), 20);
    assert.equal(r.isExplicit, true);
  });

  it('should parse Kannada "ನಿನ್ನೆ" → yesterday', () => {
    const r = parseDate('ನಿನ್ನೆ ಕೊಟ್ಟ', lexicons, now);
    assert.equal(r.date.getDate(), 20);
    assert.equal(r.isExplicit, true);
  });

  it('should parse "3 din pehle" → 3 days ago', () => {
    const r = parseDate('3 din pehle wala', lexicons, now);
    assert.equal(r.date.getDate(), 18);
    assert.equal(r.isExplicit, true);
  });

  it('should parse "5 days ago" → 5 days ago', () => {
    const r = parseDate('5 days ago', lexicons, now);
    assert.equal(r.date.getDate(), 16);
    assert.equal(r.isExplicit, true);
  });

  it('should parse DD/MM format "15/11" → Nov 15', () => {
    const r = parseDate('15/11 ko', lexicons, now);
    assert.equal(r.date.getDate(), 15);
    assert.equal(r.date.getMonth(), 10); // 0-indexed
    assert.equal(r.isExplicit, true);
  });

  it('should default to now when no date found', () => {
    const r = parseDate('kuch bhi', lexicons, now);
    assert.equal(r.date.getTime(), now.getTime());
    assert.equal(r.isExplicit, false);
    assert.equal(r.matched, null);
  });
});

// ─── Correction Handler ─────────────────────────────────────

import {
  isCorrection,
  isConfirmation,
  isDenial,
  detectIntentFromLexicon,
  extractCorrectedAmount,
} from '../client/src/voice/correctionHandler.js';

describe('Correction Handler', () => {
  it('should detect Hindi correction "galat"', () => {
    assert.equal(isCorrection('galat hai ye', lexicons), true);
  });

  it('should detect Hindi correction "nahi"', () => {
    assert.equal(isCorrection('nahi paanch sau tha', lexicons), true);
  });

  it('should detect Kannada correction "tappu"', () => {
    assert.equal(isCorrection('tappu aidu nooru', lexicons), true);
  });

  it('should NOT detect correction in normal text', () => {
    assert.equal(isCorrection('Ramesh ka paanch sau', lexicons), false);
  });

  it('should detect Hindi confirmation "haan"', () => {
    assert.equal(isConfirmation('haan theek hai', lexicons), true);
  });

  it('should detect Kannada confirmation "houdu"', () => {
    assert.equal(isConfirmation('houdu sari', lexicons), true);
  });

  it('should detect Hindi denial "nahi"', () => {
    assert.equal(isDenial('nahi cancel karo', lexicons), true);
  });

  it('should extract corrected amount from "nahi teen sau"', () => {
    const r = extractCorrectedAmount('nahi teen sau', lexicons);
    assert.equal(r.correctedAmount, 300);
  });

  it('should extract corrected amount from "galat 500"', () => {
    const r = extractCorrectedAmount('galat 500', lexicons);
    assert.equal(r.correctedAmount, 500);
  });
});

// ─── Lexicon Intent Detection ─────────────────────────────

describe('Lexicon Intent Detection', () => {
  it('should detect credit intent from "udhaar"', () => {
    assert.equal(detectIntentFromLexicon('Ramesh ka udhaar likho', lexicons), 'credit');
  });

  it('should detect payment intent from "paid"', () => {
    assert.equal(detectIntentFromLexicon('Ramesh ne paid kiya', lexicons), 'payment');
  });

  it('should detect query intent from "kitna"', () => {
    assert.equal(detectIntentFromLexicon('kitna baaki hai', lexicons), 'query');
  });

  it('should detect undo intent from "hatao"', () => {
    assert.equal(detectIntentFromLexicon('pichla hatao', lexicons), 'undo');
  });

  it('should detect Kannada credit "ಸಾಲ"', () => {
    assert.equal(detectIntentFromLexicon('ಸಾಲ ಬರೆಯಿರಿ', lexicons), 'credit');
  });

  it('should detect Kannada query "ಎಷ್ಟು"', () => {
    assert.equal(detectIntentFromLexicon('ಎಷ್ಟು ಬಾಕಿ', lexicons), 'query');
  });

  it('should return null for unrecognised input', () => {
    assert.equal(detectIntentFromLexicon('namaste', lexicons), null);
  });
});

// ─── Disclosure Manager ─────────────────────────────────────

import {
  hasDisclosed,
  markDisclosed,
  resetDisclosure,
  getDisclosureText,
  buildDisclosureTTS,
} from '../client/src/voice/disclosure.js';

describe('Disclosure Manager', () => {
  beforeEach(() => {
    resetDisclosure();
  });

  it('should start undisclosed', () => {
    assert.equal(hasDisclosed(), false);
  });

  it('should return disclosure text when not yet disclosed', () => {
    const text = getDisclosureText(templates, 'hi', { shop: 'Sharma Store' });
    assert.ok(text);
    assert.ok(text.includes('Sharma Store'));
    assert.ok(text.includes('AI'));
  });

  it('should return null after disclosure is marked', () => {
    markDisclosed();
    const text = getDisclosureText(templates, 'hi', { shop: 'Test' });
    assert.equal(text, null);
  });

  it('buildDisclosureTTS should return TTS payload and mark disclosed', () => {
    const result = buildDisclosureTTS(templates, 'hi', { shop: 'My Shop' });
    assert.ok(result);
    assert.equal(result.language, 'hi-IN');
    assert.ok(result.text.includes('My Shop'));
    assert.equal(hasDisclosed(), true);
  });

  it('buildDisclosureTTS should return null on second call', () => {
    buildDisclosureTTS(templates, 'hi', { shop: 'Test' });
    const second = buildDisclosureTTS(templates, 'hi', { shop: 'Test' });
    assert.equal(second, null);
  });
});

// ─── Barge-In Controller ────────────────────────────────────

import {
  ttsStarted,
  ttsStopped,
  checkBargeIn,
  getBargeInState,
  resetBargeIn,
} from '../client/src/voice/bargeIn.js';

describe('Barge-In Controller', () => {
  beforeEach(() => {
    resetBargeIn();
  });

  it('should not trigger barge-in when TTS is not playing', () => {
    assert.equal(checkBargeIn(0.9, 0.7), false);
  });

  it('should not trigger barge-in when energy is below threshold', () => {
    ttsStarted();
    assert.equal(checkBargeIn(0.3, 0.7), false);
  });

  it('should trigger barge-in when TTS playing and energy exceeds threshold', () => {
    ttsStarted();
    assert.equal(checkBargeIn(0.8, 0.7), true);
    const state = getBargeInState();
    assert.equal(state.bargeInCount, 1);
    assert.equal(state.isTTSPlaying, false);
  });

  it('should not trigger barge-in twice in same playback', () => {
    ttsStarted();
    assert.equal(checkBargeIn(0.9, 0.7), true);
    assert.equal(checkBargeIn(0.9, 0.7), false); // already barged in
  });

  it('should track barge-in count across playbacks', () => {
    ttsStarted();
    checkBargeIn(0.9, 0.7);
    ttsStarted(); // new playback
    checkBargeIn(0.9, 0.7);
    const state = getBargeInState();
    assert.equal(state.bargeInCount, 2);
  });

  it('should call onBargeIn callback', async () => {
    let called = false;
    const { onBargeIn } = await import('../client/src/voice/bargeIn.js');
    onBargeIn(() => { called = true; });
    ttsStarted();
    checkBargeIn(0.9, 0.7);
    assert.equal(called, true);
  });
});

// ─── Intent Extractor Module ────────────────────────────────

describe('Intent Extractor module', () => {
  it('should export extractIntent function', async () => {
    const mod = await import('../server/llm/intentExtractor.js');
    assert.equal(typeof mod.extractIntent, 'function');
  });

  it('should return unknown for empty transcript without Groq', async () => {
    const { extractIntent } = await import('../server/llm/intentExtractor.js');
    const result = await extractIntent('', {}, {});
    assert.equal(result.intent, 'unknown');
    assert.equal(result.confidence, 0);
  });

  it('should return unknown for valid transcript without Groq client', async () => {
    const { extractIntent } = await import('../server/llm/intentExtractor.js');
    const result = await extractIntent('Ramesh ka paanch sau udhaar', {}, {});
    assert.equal(result.intent, 'unknown'); // No Groq key → fallback
  });
});

// ─── Voice Pipeline ─────────────────────────────────────────

describe('Voice Pipeline', () => {
  // We test the pipeline without network (no server, no LLM)
  // It should still work using lexicon-based detection + number parsing

  let pipeline;

  beforeEach(async () => {
    pipeline = await import('../client/src/voice/voicePipeline.js');
    pipeline.resetPipeline();
    pipeline.initPipeline({
      lexicons,
      templates,
      settings: { shop: { name: 'Test Shop' } },
      serverUrl: '', // No server — LLM calls will fail gracefully
    });
  });

  it('should produce disclosure TTS on first turn', async () => {
    const r = await pipeline.processTranscript('namaste', 'hi-IN');
    assert.ok(r.ttsResponses.length > 0);
    assert.ok(r.ttsResponses[0].text.includes('AI'));
  });

  it('should NOT produce disclosure on second turn', async () => {
    await pipeline.processTranscript('namaste', 'hi-IN');
    const r2 = await pipeline.processTranscript('namaste again', 'hi-IN');
    // No disclosure in ttsResponses (it was already given)
    const hasDisclosure = r2.ttsResponses.some(t => t.text?.includes('AI'));
    assert.equal(hasDisclosure, false);
  });

  it('should detect credit intent with amount from lexicon', async () => {
    // Skip disclosure first
    await pipeline.processTranscript('hello', 'hi-IN');
    const r = await pipeline.processTranscript('Ramesh ka paanch sau udhaar', 'hi-IN');
    assert.equal(r.intent, 'credit');
    assert.equal(r.entities.amount, 500);
  });

  it('should detect payment intent with amount', async () => {
    await pipeline.processTranscript('hello', 'hi-IN');
    const r = await pipeline.processTranscript('Ramesh ne do sau diya', 'hi-IN');
    assert.equal(r.intent, 'payment');
    assert.equal(r.entities.amount, 200);
  });

  it('should detect query intent', async () => {
    await pipeline.processTranscript('hello', 'hi-IN');
    const r = await pipeline.processTranscript('Ramesh ka total kitna hai', 'hi-IN');
    assert.equal(r.intent, 'query');
  });

  it('should detect correction intent', async () => {
    await pipeline.processTranscript('hello', 'hi-IN');
    const r = await pipeline.processTranscript('galat teen sau tha', 'hi-IN');
    assert.equal(r.intent, 'correction');
    assert.equal(r.entities.correctedAmount, 300);
  });

  it('should detect confirmation when pending question exists', async () => {
    await pipeline.processTranscript('hello', 'hi-IN');
    pipeline.setPendingQuestion('confirm_credit');
    const r = await pipeline.processTranscript('haan theek hai', 'hi-IN');
    assert.equal(r.intent, 'confirmation');
    assert.equal(r.entities.confirmOrDeny, 'confirm');
  });

  it('should detect denial when pending question exists', async () => {
    await pipeline.processTranscript('hello', 'hi-IN');
    pipeline.setPendingQuestion('confirm_credit');
    const r = await pipeline.processTranscript('nahi cancel karo', 'hi-IN');
    assert.equal(r.intent, 'denial');
    assert.equal(r.entities.confirmOrDeny, 'deny');
  });

  it('should update language from STT detection', async () => {
    await pipeline.processTranscript('hello', 'kn-IN');
    const state = pipeline.getConversationState();
    assert.equal(state.language, 'kn');
  });
});
