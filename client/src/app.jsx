/**
 * Voice Khata — Root Application Component
 *
 * Implements the full Phase 7 voice-first ledger experience:
 * - Real-time conversational voice pipeline (STT, TTS, Barge-in, Lexicon & LLM parsing)
 * - Animated microphone button with 5 visual states
 * - Customer list with aggregated credit metrics and search
 * - Customer Hisab (Ledger) timeline with WhatsApp reminder deep-links
 * - New customer onboarding modal (Devanagari, Kannada, English)
 * - High-due and distance warning safety checks
 * - Full settings panel with custom API key management (Gnani, Groq, Supabase)
 * - Zero-dependency celebratory Confetti canvas for cleared balances
 */

import { useState, useEffect, useRef } from 'preact/hooks';
import { SyncBadge } from './components/SyncBadge.jsx';
import { MicButton } from './components/MicButton.jsx';
import { VoiceCard } from './components/VoiceCard.jsx';
import { CustomerList } from './components/CustomerList.jsx';
import { CustomerLedger } from './components/CustomerLedger.jsx';
import { NewCustomerModal } from './components/NewCustomerModal.jsx';
import { WarningModal } from './components/WarningModal.jsx';
import { SettingsModal } from './components/SettingsModal.jsx';
import { ReminderModal } from './components/ReminderModal.jsx';
import { DisambiguationModal } from './components/DisambiguationModal.jsx';
import { BillModal } from './components/BillModal.jsx';
import { Confetti } from './components/Confetti.jsx';

import { initSync, stopSync } from './sync/syncEngine.js';
import * as customerRepo from './db/customerRepo.js';
import * as transactionRepo from './db/transactionRepo.js';
import * as settingsRepo from './db/settingsRepo.js';
import * as reminderLogRepo from './db/reminderLogRepo.js';
import { generateUniqueCustomerId } from './utils/customerIdGenerator.js';
import { initPipeline, processTranscript } from './voice/voicePipeline.js';
import * as audioManager from './voice/audioManager.js';
import {
  addCreditWorkflow,
  addPaymentWorkflow,
  queryTotalDue,
  addPurchaseWorkflow,
} from './logic/businessRules.js';
import { checkHighDueBeforeCredit } from './logic/highDueCheck.js';
import { checkDistance } from './logic/distanceChecker.js';

export const INDIAN_LANGUAGES = [
  { code: 'auto', name: '🌐 सभी भाषाएँ (All Indian Languages - Auto)' },
  { code: 'hi', name: '🇮🇳 हिंदी / Hinglish' },
  { code: 'kn', name: '🇮🇳 ಕನ್ನಡ / Kanglish' },
  { code: 'ta', name: '🇮🇳 தமிழ் / Tanglish' },
  { code: 'te', name: '🇮🇳 తెలుగు (Telugu)' },
  { code: 'mr', name: '🇮🇳 मराठी (Marathi)' },
  { code: 'gu', name: '🇮🇳 ગુજરાતી (Gujarati)' },
  { code: 'bn', name: '🇮🇳 বাংলা (Bengali)' },
  { code: 'ml', name: '🇮🇳 മലയാളം (Malayalam)' },
  { code: 'pa', name: '🇮🇳 ਪੰਜਾਬੀ (Punjabi)' },
  { code: 'en', name: '🇬🇧 English (Indian)' },
];

export function getFullLanguageCode(lang) {
  if (!lang) return 'hi-IN';
  if (lang.includes('-')) return lang;
  const map = {
    hi: 'hi-IN', kn: 'kn-IN', ta: 'ta-IN', te: 'te-IN',
    mr: 'mr-IN', gu: 'gu-IN', bn: 'bn-IN', ml: 'ml-IN',
    pa: 'pa-IN', en: 'en-IN', auto: 'indic-auto',
    hinglish: 'hi-IN', kanglish: 'kn-IN', tanglish: 'ta-IN',
  };
  return map[lang.toLowerCase()] || `${lang}-IN`;
}

export function App() {
  // App Configuration
  const [settings, setSettings] = useState(null);
  const [templates, setTemplates] = useState({});
  const [lexicons, setLexicons] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedLanguage, setSelectedLanguage] = useState('auto');

  // Data State
  const [customers, setCustomers] = useState([]);
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [lastTxnId, setLastTxnId] = useState(null);
  const [lastCustomerId, setLastCustomerId] = useState(null);

  // Modals & Panels
  const [isNewCustomerOpen, setIsNewCustomerOpen] = useState(false);
  const [newCustomerInitialData, setNewCustomerInitialData] = useState({});
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isReminderOpen, setIsReminderOpen] = useState(false);
  const [confettiActive, setConfettiActive] = useState(false);
  const [warningState, setWarningState] = useState({ isOpen: false, type: 'high_due', data: {}, onConfirm: null });
  const [disambiguationState, setDisambiguationState] = useState({
    isOpen: false,
    customers: [],
    customerName: '',
    amount: null,
    item: '',
    intent: 'credit',
    transcript: '',
    lang: 'hi',
  });
  const [activeBill, setActiveBill] = useState(null);

  // Voice Interaction State
  const [voiceState, setVoiceState] = useState('idle'); // 'idle' | 'listening' | 'processing' | 'speaking' | 'error'
  const [voiceEnergy, setVoiceEnergy] = useState(0);
  const [liveTranscript, setLiveTranscript] = useState('');
  const [isFinalTranscript, setIsFinalTranscript] = useState(false);
  const [lastVoiceResult, setLastVoiceResult] = useState(null);
  const [distanceAlert, setDistanceAlert] = useState(null);
  const silenceTimerRef = useRef(null);
  const latestTranscriptRef = useRef('');

  // Voice Duplication & Processing Guards (Prevents 3 overlapping voices)
  const isProcessingVoiceRef = useRef(false);
  const lastProcessedTranscriptRef = useRef('');
  const lastProcessedTimeRef = useRef(0);

  // 1. Initial Load: Settings, Templates, Lexicons, and Local DB
  useEffect(() => {
    loadAppData();
  }, []);

  const loadAppData = async () => {
    try {
      // Parallel fetch of server config
      const [settingsRes, templatesRes, lexiconsRes] = await Promise.all([
        fetch('/api/settings').then((r) => r.json()).catch(() => ({})),
        fetch('/api/templates').then((r) => r.json()).catch(() => ({})),
        fetch('/api/lexicons').then((r) => r.json()).catch(() => ({})),
      ]);

      // Overlay with IndexedDB custom settings (e.g. user overrides)
      const localOverrides = await settingsRepo.getAllSettings();
      const mergedSettings = { ...settingsRes, ...localOverrides };

      setSettings(mergedSettings);
      setTemplates(templatesRes);

      const lexiconList = Object.values(lexiconsRes || {});
      setLexicons(lexiconList);

      // Initialize voice pipeline
      initPipeline({
        lexicons: lexiconList,
        templates: templatesRes,
        settings: mergedSettings,
        serverUrl: window.location.origin,
      });

      // Load customers
      await refreshCustomers();

      // Check distance from shop location
      if (mergedSettings.shop?.lat && mergedSettings.shop?.lng) {
        checkDistance(
          mergedSettings.shop.lat,
          mergedSettings.shop.lng,
          mergedSettings.limits?.distance_limit_km || 5
        ).then((dist) => {
          if (dist.warning) {
            setDistanceAlert(dist);
          }
        }).catch(() => {});
      }

      setLoading(false);
    } catch (err) {
      console.error('Initialization error:', err);
      setLoading(false);
    }
  };

  const refreshCustomers = async () => {
    try {
      const list = await customerRepo.getAllCustomers(null);
      setCustomers(list || []);
      // If a customer is currently open in ledger, refresh its object
      if (selectedCustomer) {
        const updated = await customerRepo.getCustomerById(selectedCustomer.id, null);
        if (updated) setSelectedCustomer(updated);
      }
    } catch (e) {
      console.warn('Failed to load customers from IndexedDB:', e);
    }
  };

  // Start background sync
  useEffect(() => {
    if (settings) {
      initSync(settings.sync || {});
      return () => stopSync();
    }
  }, [settings]);

  // 2. Microphone Floating Button Action
  const handleMicToggle = async () => {
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }

    if (voiceState === 'speaking') {
      audioManager.stopTTSPlayback();
      setVoiceState('idle');
      isProcessingVoiceRef.current = false;
      return;
    }

    if (voiceState === 'listening') {
      const capturedText = latestTranscriptRef.current;
      setVoiceState('processing');
      audioManager.finishRecording();

      if (capturedText && capturedText.trim()) {
        handleFinalVoiceInput(capturedText.trim());
      } else {
        // Wait up to 800ms for Gnani's final transcript to arrive
        setTimeout(() => {
          const finalCheck = latestTranscriptRef.current;
          if (finalCheck && finalCheck.trim()) {
            handleFinalVoiceInput(finalCheck.trim());
          } else {
            setVoiceState('idle');
          }
        }, 800);
      }
      return;
    }

    // Start voice session
    latestTranscriptRef.current = '';
    setLiveTranscript('');
    setIsFinalTranscript(false);
    setLastVoiceResult(null);
    setVoiceState('listening');

    // Use selected language or 'indic-auto' for all-India auto-detect
    const activeLang = selectedLanguage === 'auto' ? 'indic-auto' : getFullLanguageCode(selectedLanguage);

    await audioManager.startRecording({
      language: activeLang,
      onEnergy: (energy) => setVoiceEnergy(energy),
      onTranscript: ({ transcript, isFinal, detectedLanguage }) => {
        if (!transcript || !transcript.trim()) return;
        const cleanText = transcript.trim();
        latestTranscriptRef.current = cleanText;
        setLiveTranscript(cleanText);
        setIsFinalTranscript(isFinal);

        if (silenceTimerRef.current) {
          clearTimeout(silenceTimerRef.current);
          silenceTimerRef.current = null;
        }

        // Conversational pause detection:
        // If the utterance has an amount or action, wait 1100ms of pause.
        // If it's a short opening name (e.g. "Nisarga" or "Pavan"), allow 2200ms so they can speak the amount without cutoff.
        const hasAmountOrAction = /\d+|sau|hazar|hundred|thousand|nooru|roopayi|rupaye|udhar|jama|paid|kott|diya|kharid|khata|open/i.test(cleanText);
        const pauseDelay = hasAmountOrAction ? (isFinal ? 1000 : 1500) : 2200;

        silenceTimerRef.current = setTimeout(() => {
          audioManager.stopRecording();
          setVoiceState('processing');
          handleFinalVoiceInput(cleanText, detectedLanguage);
        }, pauseDelay);
      },
      onError: (err) => {
        console.error('Voice error:', err);
        setVoiceState('error');
        setTimeout(() => setVoiceState('idle'), 3000);
      },
    });
  };

  // 3. Process Final Speech Input Through Pipeline & Business Rules
  const handleFinalVoiceInput = async (transcript, detectedLanguage) => {
    if (!transcript || !transcript.trim()) {
      setVoiceState('idle');
      return;
    }

    const cleanText = transcript.trim();
    const now = Date.now();

    // Prevent duplicate triggers (kills the "3 voices" bug)
    if (isProcessingVoiceRef.current) return;
    if (cleanText === lastProcessedTranscriptRef.current && (now - lastProcessedTimeRef.current) < 2000) return;

    isProcessingVoiceRef.current = true;
    lastProcessedTranscriptRef.current = cleanText;
    lastProcessedTimeRef.current = now;

    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }

    audioManager.stopRecording();
    setVoiceState('processing');

    try {
      const customerRoster = (customers || []).map(c => ({
        id: c.id,
        name: c.name,
        city: c.city || '',
        phoneLast4: (c.phone || '').slice(-4),
      }));
      const result = await processTranscript(cleanText, detectedLanguage, {
        existingCustomers: customerRoster,
      });
      const rawLang = result.language || (detectedLanguage ? detectedLanguage.slice(0, 2) : (selectedLanguage !== 'auto' ? selectedLanguage : 'hi'));
      const lang = rawLang === 'auto' ? 'hi' : rawLang;

      // Note: We DO NOT call a separate disclosure speech loop here,
      // so there is NEVER an overlapping second or third voice!

      const intent = result.intent;
      const amount = result.entities?.amount;
      const customerName = result.entities?.customerName;
      const humanReply = result.humanReply;

      // Handle voice confirmation/denial for pending modals or warnings
      if (intent === 'confirmation' || result.entities?.confirmOrDeny === 'confirm') {
        if (warningState.isOpen && typeof warningState.onConfirm === 'function') {
          warningState.onConfirm();
          return;
        }
        if (isNewCustomerOpen && newCustomerInitialData?.name) {
          await handleSaveNewCustomer({
            name: newCustomerInitialData.name,
            city: 'दुकान',
            phone: '',
            whatsappConsent: true,
            initialCredit: newCustomerInitialData.amount || 0,
          });
          const successMsg = `${newCustomerInitialData.name} जी का खाता खुल गया और ₹${newCustomerInitialData.amount || 0} उधार दर्ज हो गया।`;
          setLastVoiceResult({ intent: 'credit', entities: { customerName: newCustomerInitialData.name, amount: newCustomerInitialData.amount }, message: successMsg });
          playAssistantSpeech(successMsg, lang);
          return;
        }
      }

      if (intent === 'denial' || result.entities?.confirmOrDeny === 'deny') {
        if (warningState.isOpen) setWarningState({ isOpen: false });
        if (isNewCustomerOpen) setIsNewCustomerOpen(false);
        const cancelMsg = 'ठीक है, रद्द कर दिया।';
        setLastVoiceResult({ intent: 'chat', message: cancelMsg });
        playAssistantSpeech(cancelMsg, lang);
        return;
      }

      // Check if user is answering an active customer disambiguation prompt
      if (disambiguationState.isOpen && disambiguationState.customers.length > 0) {
        const spoken = cleanText.toLowerCase();
        // Check city or phone digits or ordinals (e.g. "kengeri", "8317", "first", "pehla")
        const matched = disambiguationState.customers.find((c, idx) => {
          const cityMatch = c.city && spoken.includes(c.city.toLowerCase());
          const phoneClean = (c.phone || '').replace(/\D/g, '');
          const phoneLast4 = phoneClean.slice(-4);
          const phoneMatch = phoneClean && (spoken.includes(phoneLast4) || (amount && String(amount).includes(phoneLast4)));
          const ordinalMatch = (idx === 0 && /first|pehla|पहला|ಒಂದನೆ/i.test(spoken)) ||
                               (idx === 1 && /second|doosra|दूसरा|ಎರಡನೆ/i.test(spoken)) ||
                               (idx === 2 && /third|teesra|तीसरा|ಮೂರನೆ/i.test(spoken));
          return cityMatch || phoneMatch || ordinalMatch;
        });

        if (matched) {
          await handleSelectDisambiguatedCustomer(matched);
          return;
        }
      }

      // Handle new customer onboarding by voice (e.g. "ओपन द खाता ऑफ़ निसर्ग", "नया खाता खोलो")
      if (intent === 'new_customer') {
        setNewCustomerInitialData({ name: customerName || '', amount: amount || 0 });
        setIsNewCustomerOpen(true);
        const openMsg = customerName
          ? `${customerName} जी का नया खाता खोलने के लिए फॉर्म खुल गया है। विवरण दर्ज करें।`
          : 'नया ग्राहक जोड़ने के लिए फॉर्म खुल गया है।';
        setLastVoiceResult({ intent: 'new_customer', entities: { customerName, amount }, message: openMsg });
        playAssistantSpeech(openMsg, lang);
        return;
      }

      // Handle normal purchase (Branch A: Buy items, pay now)
      if (intent === 'purchase' || (!customerName && /खरीदा|bought|biscuit|milk|दूध|बिस्कुट|सामान|नकद|cash|पैकेट|packet|सब्जी|तेल/i.test(cleanText) && amount)) {
        await executePurchaseIntent(customerName, amount, result.entities?.item || cleanText, cleanText, lang);
      } else if (intent === 'credit' && amount) {
        await executeCreditIntent(customerName, amount, cleanText, lang);
      } else if (intent === 'credit' && !amount) {
        const askAmt = humanReply || `${customerName ? customerName + ' जी का ' : ''}कितना रुपया उधार लिखना है?`;
        setLastVoiceResult({ intent: 'credit', entities: { customerName }, message: askAmt });
        playAssistantSpeech(askAmt, lang);
      } else if (intent === 'payment' && amount) {
        await executePaymentIntent(customerName, amount, cleanText, lang);
      } else if (intent === 'payment' && !amount) {
        const askAmt = humanReply || `${customerName ? customerName + ' जी ने ' : ''}कितना रुपया जमा किया?`;
        setLastVoiceResult({ intent: 'payment', entities: { customerName }, message: askAmt });
        playAssistantSpeech(askAmt, lang);
      } else if (intent === 'query') {
        await executeQueryIntent(customerName, lang, humanReply);
      } else if (intent === 'greeting' || intent === 'chat') {
        const reply = humanReply || 'नमस्ते जी! किसका उधार लिखना है या हिसाब देखना है?';
        setLastVoiceResult({ intent, message: reply });
        playAssistantSpeech(reply, lang);
      } else if (customerName && !amount) {
        const matches = await customerRepo.searchCustomers(customerName, null);
        if (matches.length > 1) {
          setDisambiguationState({
            isOpen: true,
            customers: matches,
            customerName,
            amount: null,
            intent: 'query',
            transcript: cleanText,
            lang,
          });
          const promptMsg = templates[lang]?.multiple_customers_found || `${matches.length} ग्राहक मिले। कृपया केंगेरी या फ़ोन नंबर से सही ग्राहक चुनें।`;
          playAssistantSpeech(promptMsg, lang);
          return;
        } else if (matches.length === 1) {
          setSelectedCustomer(matches[0]);
        }
        const reply = humanReply || `हाँ जी, ${customerName} जी का खाता खुला है। उधार लिखना है या जमा करना है?`;
        setLastVoiceResult({ intent: 'chat', entities: { customerName }, message: reply });
        playAssistantSpeech(reply, lang);
      } else if (amount && !customerName) {
        const reply = humanReply || `₹${amount} किसके खाते में लिखना है भाई साहब?`;
        setLastVoiceResult({ intent: 'chat', entities: { amount }, message: reply });
        playAssistantSpeech(reply, lang);
      } else if (intent === 'undo' && lastTxnId) {
        await executeUndoIntent(lastTxnId, lang);
      } else {
        const naturalFallback = humanReply || 'हाँ जी भाई साहब! बताइए किसका हिसाब देखना है या नया उधार लिखना है?';
        setLastVoiceResult({ intent: 'chat', message: naturalFallback });
        playAssistantSpeech(naturalFallback, lang);
      }
    } catch (err) {
      console.error('Failed to process voice input:', err);
      setVoiceState('error');
      setTimeout(() => {
        setVoiceState('idle');
        isProcessingVoiceRef.current = false;
      }, 2500);
    }
  };

  // ─── Disambiguation & Intent Workflows ──────────────────────────

  const handleSelectDisambiguatedCustomer = async (chosenCustomer) => {
    const { intent, amount, item, transcript, lang } = disambiguationState;
    setDisambiguationState({ isOpen: false, customers: [] });
    setSelectedCustomer(chosenCustomer);

    if (intent === 'credit') {
      const highDueCheck = checkHighDueBeforeCredit(
        chosenCustomer,
        amount,
        settings.limits?.high_due_limit || 10000
      );
      if (highDueCheck.needsConfirmation) {
        setWarningState({
          isOpen: true,
          type: 'high_due',
          data: { name: chosenCustomer.name, newTotal: highDueCheck.newTotal, limit: highDueCheck.limit },
          onConfirm: async () => {
            setWarningState({ isOpen: false });
            await commitCredit(chosenCustomer, amount, transcript, lang, true);
          },
        });
        const warnMsg = templates[lang]?.high_due_confirm || 'बकाया सीमा पार हो जाएगी। क्या फिर भी उधार देना है?';
        setLastVoiceResult({ intent: 'credit', needsConfirmation: true, message: warnMsg });
        playAssistantSpeech(warnMsg, lang);
        return;
      }
      await commitCredit(chosenCustomer, amount, transcript, lang, false);
    } else if (intent === 'payment') {
      const deps = { transactionRepo, customerRepo, reminderLogRepo, settings, templates };
      const outcome = await addPaymentWorkflow({
        customerId: chosenCustomer.id,
        customer: chosenCustomer,
        amount,
        source: 'voice',
        voiceTranscript: transcript,
      }, deps);
      setLastTxnId(outcome.txn.id);
      setLastCustomerId(chosenCustomer.id);
      await refreshCustomers();
      if (outcome.dueCleared) setConfettiActive(true);
      setLastVoiceResult({
        intent: 'payment',
        entities: { customerName: chosenCustomer.name, amount },
        message: outcome.ttsText,
      });
      playAssistantSpeech(outcome.ttsText, lang);
    } else if (intent === 'purchase') {
      await executePurchaseIntent(chosenCustomer.name, amount, item, transcript, lang, chosenCustomer);
    } else if (intent === 'query') {
      const deps = { customerRepo, settings, templates };
      const outcome = await queryTotalDue(chosenCustomer, deps);
      setLastVoiceResult({
        intent: 'query',
        entities: { customerName: chosenCustomer.name, amount: chosenCustomer.totalDue },
        message: outcome.ttsText,
      });
      playAssistantSpeech(outcome.ttsText, lang);
    }
  };

  const executeCreditIntent = async (rawName, amount, transcript, lang) => {
    let customer = null;
    if (rawName) {
      const matches = await customerRepo.searchCustomers(rawName, null);
      if (matches.length > 1) {
        setDisambiguationState({
          isOpen: true,
          customers: matches,
          customerName: rawName,
          amount,
          intent: 'credit',
          transcript,
          lang,
        });
        const promptMsg = templates[lang]?.multiple_customers_found || `${matches.length} ग्राहक मिले। कृपया केंगेरी या फ़ोन नंबर से सही ग्राहक चुनें।`;
        setLastVoiceResult({ intent: 'credit', entities: { customerName: rawName, amount }, message: promptMsg });
        playAssistantSpeech(promptMsg, lang);
        return;
      } else if (matches.length === 1) {
        customer = matches[0];
      }
    } else if (selectedCustomer) {
      customer = selectedCustomer;
    }

    if (!customer) {
      // Acoustic & token fallback: check if any customer was spoken in transcript
      const fallbackCustomer = customerRepo.resolveCustomerFromTranscript(transcript, rawName, customers);
      if (fallbackCustomer) {
        customer = fallbackCustomer;
      }
    }

    if (!customer) {
      // Customer not found: open New Customer onboarding with prefilled name & amount
      setNewCustomerInitialData({ name: rawName || '', amount });
      setIsNewCustomerOpen(true);
      const askMsg = rawName
        ? `${rawName} जी का खाता नहीं मिला। क्या मैं उनका नया खाता खोल दूँ?`
        : (templates[lang]?.ask_name || 'नया ग्राहक जोड़ने के लिए विवरण भरें');
      setLastVoiceResult({ intent: 'credit', entities: { customerName: rawName, amount }, message: askMsg });
      playAssistantSpeech(askMsg, lang);
      return;
    }

    // Check high due limit
    const highDueCheck = checkHighDueBeforeCredit(
      customer,
      amount,
      settings.limits?.high_due_limit || 10000
    );

    if (highDueCheck.needsConfirmation) {
      setWarningState({
        isOpen: true,
        type: 'high_due',
        data: { name: customer.name, newTotal: highDueCheck.newTotal, limit: highDueCheck.limit },
        onConfirm: async () => {
          setWarningState({ isOpen: false });
          await commitCredit(customer, amount, transcript, lang, true);
        },
      });
      const warnMsg = templates[lang]?.high_due_confirm || 'बकाया सीमा पार हो जाएगी। क्या फिर भी उधार देना है?';
      setLastVoiceResult({ intent: 'credit', needsConfirmation: true, message: warnMsg });
      playAssistantSpeech(warnMsg, lang);
      return;
    }

    await commitCredit(customer, amount, transcript, lang, false);
  };

  const commitCredit = async (customer, amount, transcript, lang, confirmed) => {
    const deps = { transactionRepo, customerRepo, reminderLogRepo, settings, templates };
    const outcome = await addCreditWorkflow({
      customerId: customer.id,
      customer,
      amount,
      source: 'voice',
      voiceTranscript: transcript,
      confirmed,
    }, deps);

    if (!outcome.success && outcome.needsConfirmation) {
      setWarningState({
        isOpen: true,
        type: 'high_due',
        data: { name: customer.name, newTotal: outcome.projectedDue, limit: settings?.limits?.high_due_limit || 10000 },
        onConfirm: async () => {
          setWarningState({ isOpen: false });
          await commitCredit(customer, amount, transcript, lang, true);
        },
      });
      playAssistantSpeech(outcome.confirmText, lang);
      return;
    }

    setLastTxnId(outcome.txn.id);
    setLastCustomerId(customer.id);
    await refreshCustomers();

    setLastVoiceResult({
      intent: 'credit',
      entities: { customerName: customer.name, amount },
      message: outcome.ttsText,
    });
    playAssistantSpeech(outcome.ttsText, lang);
  };

  const executePaymentIntent = async (rawName, amount, transcript, lang) => {
    let customer = null;
    if (rawName) {
      const matches = await customerRepo.searchCustomers(rawName, null);
      if (matches.length > 1) {
        setDisambiguationState({
          isOpen: true,
          customers: matches,
          customerName: rawName,
          amount,
          intent: 'payment',
          transcript,
          lang,
        });
        const promptMsg = templates[lang]?.multiple_customers_found || `${matches.length} ग्राहक मिले। कृपया केंगेरी या फ़ोन नंबर से सही ग्राहक चुनें।`;
        setLastVoiceResult({ intent: 'payment', entities: { customerName: rawName, amount }, message: promptMsg });
        playAssistantSpeech(promptMsg, lang);
        return;
      } else if (matches.length === 1) {
        customer = matches[0];
      }
    } else if (selectedCustomer) {
      customer = selectedCustomer;
    }

    if (!customer) {
      const fallbackCustomer = customerRepo.resolveCustomerFromTranscript(transcript, rawName, customers);
      if (fallbackCustomer) {
        customer = fallbackCustomer;
      }
    }

    if (!customer) {
      if (rawName) {
        setNewCustomerInitialData({ name: rawName, amount: 0 });
        setIsNewCustomerOpen(true);
        const notFoundMsg = `${rawName} जी का खाता नहीं मिला। क्या मैं उनका नया खाता खोल दूँ?`;
        setLastVoiceResult({ intent: 'payment', entities: { customerName: rawName, amount }, message: notFoundMsg });
        playAssistantSpeech(notFoundMsg, lang);
      } else {
        const notFoundMsg = templates[lang]?.error_no_name || 'ग्राहक का नाम बताइए';
        setLastVoiceResult({ intent: 'payment', message: notFoundMsg });
        playAssistantSpeech(notFoundMsg, lang);
      }
      return;
    }

    const deps = { transactionRepo, customerRepo, reminderLogRepo, settings, templates };
    const outcome = await addPaymentWorkflow({
      customerId: customer.id,
      customer,
      amount,
      source: 'voice',
      voiceTranscript: transcript,
    }, deps);

    setLastTxnId(outcome.txn.id);
    setLastCustomerId(customer.id);
    await refreshCustomers();

    // If payment fully cleared balance, trigger celebration confetti!
    if (outcome.dueCleared) {
      setConfettiActive(true);
    }

    setLastVoiceResult({
      intent: 'payment',
      entities: { customerName: customer.name, amount },
      message: outcome.ttsText,
    });
    playAssistantSpeech(outcome.ttsText, lang);
  };

  const executeQueryIntent = async (rawName, lang, humanReply) => {
    let customer = null;
    if (rawName) {
      const matches = await customerRepo.searchCustomers(rawName, null);
      if (matches.length > 1) {
        setDisambiguationState({
          isOpen: true,
          customers: matches,
          customerName: rawName,
          amount: null,
          intent: 'query',
          transcript: '',
          lang,
        });
        const promptMsg = templates[lang]?.multiple_customers_found || `${matches.length} ग्राहक मिले। कृपया केंगेरी या फ़ोन नंबर से सही ग्राहक चुनें।`;
        playAssistantSpeech(promptMsg, lang);
        return;
      } else if (matches.length === 1) {
        customer = matches[0];
        setSelectedCustomer(customer);
      }
    } else if (selectedCustomer) {
      customer = selectedCustomer;
    }

    if (!customer) {
      const fallbackCustomer = customerRepo.resolveCustomerFromTranscript(rawName, rawName, customers);
      if (fallbackCustomer) {
        customer = fallbackCustomer;
        setSelectedCustomer(customer);
      }
    }

    if (!customer) {
      const notFoundMsg = rawName
        ? `${rawName} जी का खाता नहीं मिला।`
        : (humanReply || templates[lang]?.error_no_name || 'ग्राहक का नाम बताइए');
      setLastVoiceResult({ intent: 'query', message: notFoundMsg });
      playAssistantSpeech(notFoundMsg, lang);
      return;
    }

    const deps = { customerRepo, settings, templates };
    const outcome = await queryTotalDue(customer, deps);
    setLastVoiceResult({
      intent: 'query',
      entities: { customerName: customer.name, amount: customer.totalDue },
      message: outcome.ttsText,
    });
    playAssistantSpeech(outcome.ttsText, lang);
  };

  const executePurchaseIntent = async (rawName, amount, item, transcript, lang, preselectedCustomer = null) => {
    let customer = preselectedCustomer;
    if (!customer && rawName) {
      const matches = await customerRepo.searchCustomers(rawName, null);
      if (matches.length > 1) {
        setDisambiguationState({
          isOpen: true,
          customers: matches,
          customerName: rawName,
          amount,
          item: item || 'सामान',
          intent: 'purchase',
          transcript,
          lang,
        });
        const promptMsg = templates[lang]?.multiple_customers_found || `${matches.length} ग्राहक मिले। कृपया सही ग्राहक चुनें।`;
        playAssistantSpeech(promptMsg, lang);
        return;
      } else if (matches.length === 1) {
        customer = matches[0];
      }
    } else if (!customer && selectedCustomer) {
      customer = selectedCustomer;
    }

    const itemDesc = item || 'दुकान सामान (General Items)';
    const deps = { transactionRepo, customerRepo, settings, templates };
    const outcome = await addPurchaseWorkflow({
      customerId: customer?.id || 'walk_in',
      customer,
      amount: amount || 0,
      item: itemDesc,
      voiceTranscript: transcript,
      source: 'voice',
    }, deps);

    setLastTxnId(outcome.txn.id);
    if (customer) {
      setLastCustomerId(customer.id);
      await refreshCustomers();
    }
    setActiveBill(outcome.bill);

    setLastVoiceResult({
      intent: 'purchase',
      entities: { customerName: customer?.name || 'नकद ग्राहक', amount, item: itemDesc },
      message: outcome.ttsText,
    });
    playAssistantSpeech(outcome.ttsText, lang);
  };

  const executeUndoIntent = async (txnId, lang) => {
    try {
      await transactionRepo.reverseTransaction(txnId, 'Voice undo command');
      await refreshCustomers();
      const undoMsg = 'पिछला लेन-देन रद्द कर दिया गया है';
      setLastVoiceResult({ intent: 'undo', message: undoMsg });
      playAssistantSpeech(undoMsg, lang);
    } catch (e) {
      console.warn('Undo failed:', e);
    }
  };

  // Helper to play TTS cleanly (Single Voice Channel)
  const playAssistantSpeech = (text, language = 'hi') => {
    if (!text) {
      setVoiceState('idle');
      isProcessingVoiceRef.current = false;
      return;
    }

    const langCode = getFullLanguageCode(language);
    const voiceName = audioManager.getVoiceForLanguage(langCode);

    setVoiceState('speaking');
    audioManager.speakText({
      text,
      language: langCode,
      voice: voiceName,
      onStart: () => setVoiceState('speaking'),
      onEnd: () => {
        setVoiceState('idle');
        isProcessingVoiceRef.current = false;
      },
      onError: () => {
        setVoiceState('idle');
        isProcessingVoiceRef.current = false;
      },
    });
  };

  // 4. Create New Customer Submission
  const handleSaveNewCustomer = async (data) => {
    try {
      const created = await customerRepo.createCustomer(
        {
          name: data.name,
          city: data.city,
          phone: data.phone,
          whatsappConsent: data.whatsappConsent,
        },
        settings,
        null,
        generateUniqueCustomerId
      );

      // If initial credit was requested, add it
      if (data.initialCredit > 0) {
        await addCreditWorkflow({
          customer: created,
          customerId: created.id,
          amount: data.initialCredit,
          source: 'manual',
        }, { transactionRepo, customerRepo, reminderLogRepo, settings, templates });
      }

      setIsNewCustomerOpen(false);
      await refreshCustomers();
      setSelectedCustomer(created);
    } catch (err) {
      alert(`ग्राहक जोड़ने में त्रुटि: ${err.message}`);
    }
  };

  if (loading) {
    return (
      <div class="loading-screen">
        <div class="loading-spinner"></div>
        <p>वॉइस खाता लोड हो रहा है...</p>
      </div>
    );
  }

  return (
    <div style={appContainerStyle}>
      {/* Confetti Celebration Engine */}
      <Confetti
        active={confettiActive}
        onComplete={() => setConfettiActive(false)}
      />

      {/* Header */}
      <header style={headerStyle}>
        <div>
          <h1 style={titleStyle}>{settings?.shop?.name || 'वॉइस खाता'}</h1>
          <span style={subtitleStyle}>Voice-First Udhaar Ledger</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          {/* Indian Language Selector */}
          <select
            value={selectedLanguage}
            onChange={(e) => {
              const newLang = e.target.value;
              setSelectedLanguage(newLang);
              settingsRepo.setSetting('language_override', newLang);
            }}
            style={langSelectStyle}
            title="भाषा चुनें (Select Language / Dialect)"
            aria-label="Language Selector"
          >
            {INDIAN_LANGUAGES.map((l) => (
              <option key={l.code} value={l.code} style={{ color: '#111', background: '#fff' }}>
                {l.name}
              </option>
            ))}
          </select>
          <button
            style={reminderHeaderBtnStyle}
            onClick={() => setIsReminderOpen(true)}
            aria-label="Daily Reminders"
            title="आज के तगादे (Reminders)"
          >
            📢 तगादा
          </button>
          <SyncBadge />
          <a
            href="/store.html"
            style={{
              background: 'rgba(255, 255, 255, 0.2)',
              color: '#ffffff',
              border: '1px solid rgba(255, 255, 255, 0.4)',
              borderRadius: '20px',
              padding: '6px 12px',
              fontSize: '12px',
              fontWeight: '600',
              textDecoration: 'none',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
            }}
            title="किराना स्टोर व्यू (Mera Kirana Store UI)"
          >
            🏪 किराना स्टोर
          </a>
          <button
            style={iconBtnStyle}
            onClick={() => setIsSettingsOpen(true)}
            aria-label="Settings"
            title="सेटिंग्स"
          >
            ⚙️
          </button>
        </div>
      </header>

      {/* Geofence Distance Warning Banner */}
      {distanceAlert && (
        <div style={distanceBannerStyle}>
          📍 ध्यान दें: आप दुकान से {distanceAlert.distanceKm} किमी दूर हैं। (अनुशंसित: &lt; {distanceAlert.limitKm} किमी)
        </div>
      )}

      {/* Main Content: Customer Ledger Dashboard */}
      <main style={{ flex: 1, overflowY: 'auto' }}>
        <CustomerList
          customers={customers}
          onSelectCustomer={(c) => setSelectedCustomer(c)}
          onAddNewCustomer={() => {
            setNewCustomerInitialData({});
            setIsNewCustomerOpen(true);
          }}
        />
      </main>

      {/* Real-time Voice Interaction Card Overlay */}
      <VoiceCard
        transcript={liveTranscript}
        isFinal={isFinalTranscript}
        status={voiceState}
        lastResult={lastVoiceResult}
        onClose={() => setLastVoiceResult(null)}
        onConfirm={warningState.onConfirm}
        onCancel={() => setWarningState({ isOpen: false })}
      />

      {/* Floating Action Mic Button */}
      <MicButton
        state={voiceState}
        energy={voiceEnergy}
        onClick={handleMicToggle}
      />

      {/* Customer Hisab (Ledger) Modal */}
      {selectedCustomer && (
        <CustomerLedger
          customer={selectedCustomer}
          templates={templates}
          shopName={settings?.shop?.name}
          onClose={() => setSelectedCustomer(null)}
          onAddCredit={async (cust, amt) => {
            await commitCredit(cust, amt, 'Manual credit entry', cust.preferredLanguage || 'hi', true);
          }}
          onAddPayment={async (cust, amt) => {
            await executePaymentIntent(cust.name, amt, 'Manual payment entry', cust.preferredLanguage || 'hi');
          }}
          onReverseTxn={async (txnId) => {
            await executeUndoIntent(txnId, selectedCustomer.preferredLanguage || 'hi');
          }}
        />
      )}

      {/* New Customer Onboarding Modal */}
      <NewCustomerModal
        key={isNewCustomerOpen ? `${newCustomerInitialData?.name || ''}_${newCustomerInitialData?.amount || ''}` : 'closed'}
        isOpen={isNewCustomerOpen}
        initialData={newCustomerInitialData}
        onClose={() => setIsNewCustomerOpen(false)}
        onSubmit={handleSaveNewCustomer}
      />

      {/* Business Rule Warning Modal */}
      <WarningModal
        isOpen={warningState.isOpen}
        type={warningState.type}
        data={warningState.data}
        onConfirm={warningState.onConfirm}
        onCancel={() => setWarningState({ isOpen: false })}
      />

      {/* Daily WhatsApp Reminder Modal */}
      <ReminderModal
        isOpen={isReminderOpen}
        settings={settings}
        templates={templates}
        onClose={() => setIsReminderOpen(false)}
        onReminderSent={() => refreshCustomers()}
      />

      {/* Settings & Custom API Key Modal */}
      <SettingsModal
        isOpen={isSettingsOpen}
        initialSettings={settings}
        onClose={() => setIsSettingsOpen(false)}
        onSave={(updated) => {
          setSettings((prev) => ({ ...prev, ...updated }));
        }}
      />

      {/* Disambiguation Modal for Multiple Same-Name Matches */}
      <DisambiguationModal
        isOpen={disambiguationState.isOpen}
        customers={disambiguationState.customers}
        customerName={disambiguationState.customerName}
        amount={disambiguationState.amount}
        intent={disambiguationState.intent}
        onSelect={handleSelectDisambiguatedCustomer}
        onClose={() => setDisambiguationState({ isOpen: false, customers: [] })}
      />

      {/* Bill Receipt Modal for Normal Purchases */}
      <BillModal
        isOpen={!!activeBill}
        bill={activeBill}
        shop={settings.shop || {}}
        onClose={() => setActiveBill(null)}
      />
    </div>
  );
}

const appContainerStyle = {
  minHeight: '100vh',
  display: 'flex',
  flexDirection: 'column',
  background: '#f4f6f8',
  position: 'relative',
};

const headerStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  padding: '12px 18px',
  background: '#1a73e8',
  color: 'white',
  boxShadow: '0 2px 8px rgba(0,0,0,0.12)',
  position: 'sticky',
  top: 0,
  zIndex: 100,
};

const titleStyle = {
  fontSize: '18px',
  fontWeight: '700',
  margin: 0,
  letterSpacing: '0.2px',
};

const subtitleStyle = {
  fontSize: '11px',
  opacity: 0.85,
};

const iconBtnStyle = {
  background: 'rgba(255,255,255,0.2)',
  border: 'none',
  color: 'white',
  width: '36px',
  height: '36px',
  borderRadius: '50%',
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  fontSize: '16px',
};

const reminderHeaderBtnStyle = {
  background: 'rgba(255, 255, 255, 0.25)',
  border: '1px solid rgba(255,255,255,0.35)',
  color: 'white',
  padding: '6px 12px',
  borderRadius: '16px',
  fontWeight: '600',
  fontSize: '13px',
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  gap: '4px',
};

const distanceBannerStyle = {
  background: '#fff3e0',
  color: '#e65100',
  padding: '8px 16px',
  fontSize: '12px',
  fontWeight: '600',
  textAlign: 'center',
  borderBottom: '1px solid #ffe0b2',
};

const langSelectStyle = {
  background: 'rgba(255, 255, 255, 0.22)',
  color: '#ffffff',
  border: '1px solid rgba(255, 255, 255, 0.4)',
  borderRadius: '16px',
  padding: '6px 10px',
  fontSize: '12px',
  fontWeight: '600',
  cursor: 'pointer',
  outline: 'none',
  maxWidth: '220px',
};
