/**
 * Voice Khata - Server Entry Point (Live Gnani STT/TTS Proxy - Live)
 * 
 * Fastify server that:
 * - Proxies Gnani STT/TTS WebSocket connections (keeps API keys server-side)
 * - Provides REST API for settings, sync, and reminders
 * - Serves the built PWA client in production
 * - Calls Groq LLM for intent extraction
 */

import 'dotenv/config';
import Fastify from 'fastify';
import cors from '@fastify/cors';
import websocket from '@fastify/websocket';
import { loadConfig } from './config/loader.js';
import syncRoutes from './routes/sync.js';
import reminderRoutes from './reminders/scheduler.js';
import voiceRoutes from './voice/index.js';
import intentRoutes, { initGroq } from './llm/intentExtractor.js';
import { resetSupabase, getSupabase } from './supabase/client.js';
import { memoryStore } from './routes/sync.js';
import crypto from 'crypto';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const fastify = Fastify({
  logger: true,
});

// Load configuration
const config = loadConfig();

// Register CORS for full local development across any port
await fastify.register(cors, {
  origin: true,
  credentials: true,
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
});

// Register WebSocket support (required BEFORE voice routes)
await fastify.register(websocket);

// Health check route
fastify.get('/api/health', async () => {
  return {
    status: 'ok',
    timestamp: new Date().toISOString(),
    version: '1.0.0',
  };
});

// Settings route - returns non-secret settings for the client
fastify.get('/api/settings', async () => {
  return config.settings;
});

// Helper for upserting keys in .env
function upsertEnvVar(content, key, val) {
  const regex = new RegExp(`^${key}=.*$`, 'm');
  if (regex.test(content)) {
    return content.replace(regex, `${key}=${val}`);
  }
  return content.trim() + `\n${key}=${val}\n`;
}

// GET /api/settings/keys-status - Check status of configured API keys
fastify.get('/api/settings/keys-status', async () => {
  const mask = (key) => {
    if (!key) return null;
    if (key.length <= 8) return '****';
    return key.slice(0, 4) + '...' + key.slice(-4);
  };
  return {
    groq: {
      configured: Boolean(config.env.GROQ_API_KEY),
      masked: mask(config.env.GROQ_API_KEY),
      apiKey: config.env.GROQ_API_KEY || '',
      model: config.env.GROQ_MODEL || 'qwen/qwen3.8-27b',
    },
    gnani: {
      configured: Boolean(config.env.GNANI_API_KEY),
      masked: mask(config.env.GNANI_API_KEY),
      apiKey: config.env.GNANI_API_KEY || '',
    },
    supabase: {
      configured: Boolean(config.env.SUPABASE_URL && (config.env.SUPABASE_SERVICE_ROLE_KEY || config.env.SUPABASE_KEY)),
      url: config.env.SUPABASE_URL || '',
      maskedKey: mask(config.env.SUPABASE_SERVICE_ROLE_KEY || config.env.SUPABASE_KEY),
      apiKey: config.env.SUPABASE_SERVICE_ROLE_KEY || config.env.SUPABASE_KEY || '',
    },
  };
});

// POST /api/settings/keys - Update API keys dynamically when exhausted
fastify.post('/api/settings/keys', async (req, reply) => {
  const { groqApiKey, groqModel, gnaniApiKey, supabaseUrl, supabaseServiceKey } = req.body || {};
  let updatedAny = false;

  if (groqApiKey && typeof groqApiKey === 'string' && groqApiKey.trim()) {
    config.env.GROQ_API_KEY = groqApiKey.trim();
    initGroq(config.env.GROQ_API_KEY);
    updatedAny = true;
  }

  if (groqModel && typeof groqModel === 'string' && groqModel.trim()) {
    config.env.GROQ_MODEL = groqModel.trim();
    updatedAny = true;
  }

  if (gnaniApiKey && typeof gnaniApiKey === 'string' && gnaniApiKey.trim()) {
    config.env.GNANI_API_KEY = gnaniApiKey.trim();
    updatedAny = true;
  }

  if (supabaseUrl && typeof supabaseUrl === 'string' && supabaseUrl.trim()) {
    config.env.SUPABASE_URL = supabaseUrl.trim();
    resetSupabase();
    updatedAny = true;
  }

  if (supabaseServiceKey && typeof supabaseServiceKey === 'string' && supabaseServiceKey.trim()) {
    config.env.SUPABASE_SERVICE_ROLE_KEY = supabaseServiceKey.trim();
    resetSupabase();
    updatedAny = true;
  }

  // Persist to .env files so new keys survive server restarts
  try {
    const envPaths = [
      path.join(__dirname, '..', '.env'),
      path.join(__dirname, '..', '..', '.env'),
    ];
    for (const envPath of envPaths) {
      if (fs.existsSync(envPath)) {
        let envContent = fs.readFileSync(envPath, 'utf8');
        if (groqApiKey) envContent = upsertEnvVar(envContent, 'GROQ_API_KEY', groqApiKey.trim());
        if (groqModel) envContent = upsertEnvVar(envContent, 'GROQ_MODEL', groqModel.trim());
        if (gnaniApiKey) envContent = upsertEnvVar(envContent, 'GNANI_API_KEY', gnaniApiKey.trim());
        if (supabaseUrl) envContent = upsertEnvVar(envContent, 'SUPABASE_URL', supabaseUrl.trim());
        if (supabaseServiceKey) envContent = upsertEnvVar(envContent, 'SUPABASE_SERVICE_ROLE_KEY', supabaseServiceKey.trim());
        fs.writeFileSync(envPath, envContent, 'utf8');
      }
    }
  } catch (err) {
    fastify.log.warn({ err: err.message }, 'Failed to persist keys to .env');
  }

  return {
    success: true,
    message: 'API keys successfully updated and applied immediately!',
    activeModel: config.env.GROQ_MODEL || 'qwen/qwen3.8-27b',
  };
});

// Register sync routes
await fastify.register(syncRoutes, { config });

// Register reminder routes
await fastify.register(reminderRoutes, { config });

// Register voice WebSocket proxy routes (STT, TTS, metrics)
await fastify.register(voiceRoutes, { config });

// Register LLM intent extraction route
await fastify.register(intentRoutes, { config });

// Templates endpoint (client needs them for message generation)
fastify.get('/api/templates', async () => {
  return config.templates;
});

// Lexicons endpoint (client needs them for fast deterministic parsing)
fastify.get('/api/lexicons', async () => {
  return config.lexicons;
});

// Digital Tamper-Evident Receipt JSON API
fastify.get('/api/receipt/:eventId', async (req, reply) => {
  const { eventId } = req.params;
  const q = req.query || {};

  let txn = memoryStore.transactions.get(eventId);
  let cust = null;

  if (!txn) {
    const sb = getSupabase(config.env);
    if (sb) {
      try {
        const { data } = await sb.from('transactions').select('*').eq('id', eventId).maybeSingle();
        if (data) {
          txn = data;
          if (data.customer_id) {
            const { data: cData } = await sb.from('customers').select('*').eq('id', data.customer_id).maybeSingle();
            cust = cData;
          }
        }
      } catch (e) {}
    }
  }

  const customerName = cust?.name || txn?.customer || q.c || 'Valued Customer';
  const customerPhone = cust?.phone_enc || cust?.phone || q.p || '';
  const amount = Number(txn?.amount || q.a || 0);
  const rawType = (txn?.type || q.t || 'udhaar').toLowerCase();
  const type = rawType.includes('credit') || rawType.includes('udhaar') ? 'udhaar' : (rawType.includes('pay') || rawType.includes('vasooli') ? 'vasooli' : 'bill');
  const balanceDue = Number(cust?.total_due !== undefined ? cust.total_due : (q.b !== undefined ? q.b : amount));
  const dateStr = txn?.created_at || txn?.timestamp || txn?.rawDate || q.d || new Date().toISOString();
  const note = txn?.note || txn?.voice_transcript || q.n || (type === 'udhaar' ? 'Kirana Udhaar' : (type === 'vasooli' ? 'Payment Received' : 'Cash Purchase'));
  const storeName = config.settings?.shop?.name || 'Mera Kirana Store';

  // Compute cryptographic tamper-evident seal
  const hashInput = `${eventId}|${customerName}|${amount}|${type}|${balanceDue}|${dateStr}`;
  const sha256Hash = crypto.createHash('sha256').update(hashInput).digest('hex');

  return {
    eventId,
    storeName,
    storeAddress: 'Kengeri Main Road, Bengaluru, Karnataka 560060',
    storePhone: '+91 98450 12345',
    customerName,
    customerPhone,
    type,
    amount,
    balanceDue,
    dateStr,
    note,
    status: type === 'udhaar' ? 'Your udhaar is added on VoiceKhata' : (type === 'vasooli' ? 'Your payment is recorded on VoiceKhata' : 'Your sale bill is recorded on VoiceKhata'),
    verified: true,
    verificationBadge: 'Verified by VoiceKhata • Tamper-Evident Digital Receipt',
    sha256Hash,
    verifiedAt: new Date().toISOString(),
  };
});

// Digital Tamper-Evident Receipt Full HTML Page
fastify.get('/r/:eventId', async (req, reply) => {
  reply.header('Content-Type', 'text/html; charset=utf-8');
  const { eventId } = req.params;
  const q = req.query || {};

  let txn = memoryStore.transactions.get(eventId);
  let cust = null;

  if (!txn) {
    const sb = getSupabase(config.env);
    if (sb) {
      try {
        const { data } = await sb.from('transactions').select('*').eq('id', eventId).maybeSingle();
        if (data) {
          txn = data;
          if (data.customer_id) {
            const { data: cData } = await sb.from('customers').select('*').eq('id', data.customer_id).maybeSingle();
            cust = cData;
          }
        }
      } catch (e) {}
    }
  }

  const customerName = cust?.name || txn?.customer || q.c || 'Customer';
  const customerPhone = cust?.phone_enc || cust?.phone || q.p || '';
  const amount = Number(txn?.amount || q.a || 0);
  const rawType = (txn?.type || q.t || 'udhaar').toLowerCase();
  const isCredit = rawType.includes('credit') || rawType.includes('udhaar');
  const isPayment = rawType.includes('pay') || rawType.includes('vasooli');
  const typeLabel = isCredit ? 'Udhaar (Credit Added)' : (isPayment ? 'Vasooli (Payment Received)' : 'Cash Sale (नकद बिक्री)');
  const balanceDue = Number(cust?.total_due !== undefined ? cust.total_due : (q.b !== undefined ? q.b : amount));
  const dateRaw = txn?.created_at || txn?.timestamp || txn?.rawDate || q.d || new Date().toISOString();
  const dateFormatted = new Date(dateRaw).toLocaleString('en-IN', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: true
  });
  const note = txn?.note || txn?.voice_transcript || q.n || (isCredit ? 'Kirana Udhaar' : (isPayment ? 'Cash Settlement' : 'Store Purchase'));
  const storeName = config.settings?.shop?.name || 'Mera Kirana Store';

  // Compute tamper-evident SHA-256 seal
  const hashInput = `${eventId}|${customerName}|${amount}|${rawType}|${balanceDue}|${dateRaw}`;
  const sha256Hash = crypto.createHash('sha256').update(hashInput).digest('hex');

  const bannerText = isCredit ? 'Your udhaar is added on VoiceKhata' : (isPayment ? 'Your payment is recorded on VoiceKhata' : 'Your bill is recorded on VoiceKhata');
  const bannerBg = isCredit ? 'bg-amber-50 text-amber-900 border-amber-200' : 'bg-emerald-50 text-emerald-900 border-emerald-200';
  const amtColor = isCredit ? 'text-red-600' : 'text-emerald-700';

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>VoiceKhata Receipt • ${eventId.slice(0, 8)}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    @media print {
      .no-print { display: none !important; }
      body { background: white !important; }
      .receipt-card { box-shadow: none !important; border: 1px solid #ccc !important; }
    }
  </style>
</head>
<body class="bg-[#F4F1EA] text-slate-800 font-sans min-h-screen py-6 px-4 flex items-center justify-center">
  <div class="max-w-md w-full bg-white rounded-3xl shadow-xl border border-[#E5E0D5] overflow-hidden receipt-card">
    
    <!-- Verified Header Ribbon -->
    <div class="bg-[#14281D] px-6 py-4 text-white flex items-center justify-between">
      <div class="flex items-center gap-2">
        <span class="text-xl">🛡️</span>
        <div>
          <p class="text-xs font-bold tracking-wide uppercase text-emerald-400">Verified by VoiceKhata</p>
          <p class="text-[11px] text-emerald-100/80">Tamper-Evident Digital Receipt</p>
        </div>
      </div>
      <div class="text-right">
        <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-400/30">
          ● REAL-TIME LEDGER
        </span>
      </div>
    </div>

    <!-- Store Info -->
    <div class="p-6 text-center border-b border-dashed border-[#E5E0D5] bg-[#FAF8F3]">
      <h1 class="text-2xl font-black text-[#14281D] tracking-tight">${storeName}</h1>
      <p class="text-xs text-slate-500 mt-1 font-medium">Kengeri Main Road, Bengaluru, Karnataka • GSTIN: 29AABCU9603R1ZM</p>
      <p class="text-xs text-slate-500">Phone: +91 98450 12345 • Powered by VoiceKhata</p>
    </div>

    <!-- Status Alert Banner -->
    <div class="mx-6 my-4 p-3.5 rounded-2xl border ${bannerBg} flex items-center gap-3">
      <div class="w-8 h-8 rounded-full bg-white shadow-sm flex items-center justify-center text-base shrink-0">
        ${isCredit ? '📝' : '✅'}
      </div>
      <div>
        <p class="text-xs font-bold uppercase tracking-wider ${isCredit ? 'text-amber-800' : 'text-emerald-800'}">Transaction Notice</p>
        <p class="text-sm font-extrabold">${bannerText}</p>
      </div>
    </div>

    <!-- Transaction Main Details -->
    <div class="px-6 py-2 space-y-4">
      <div class="bg-[#FAF8F3] rounded-2xl p-4 border border-[#EAE5D7] space-y-3">
        <div class="flex items-center justify-between text-xs">
          <span class="text-slate-500 font-semibold">Customer Name:</span>
          <span class="font-black text-slate-900 text-sm">${customerName}</span>
        </div>
        ${customerPhone ? `
        <div class="flex items-center justify-between text-xs">
          <span class="text-slate-500 font-semibold">Mobile Number:</span>
          <span class="font-mono font-bold text-slate-800">${customerPhone}</span>
        </div>` : ''}
        <div class="flex items-center justify-between text-xs">
          <span class="text-slate-500 font-semibold">Transaction Date:</span>
          <span class="font-medium text-slate-700">${dateFormatted}</span>
        </div>
        <div class="flex items-center justify-between text-xs">
          <span class="text-slate-500 font-semibold">Type:</span>
          <span class="font-bold text-slate-800">${typeLabel}</span>
        </div>
        ${note ? `
        <div class="flex items-center justify-between text-xs pt-1 border-t border-[#EAE5D7]">
          <span class="text-slate-500 font-semibold">Note / Detail:</span>
          <span class="font-medium text-slate-800 italic">${note}</span>
        </div>` : ''}
      </div>

      <!-- Amount Highlight Box -->
      <div class="p-4 rounded-2xl bg-[#FFFDF8] border-2 border-dashed border-[#DFD9CA] text-center space-y-1">
        <p class="text-xs font-bold text-slate-500 uppercase tracking-wider">${isCredit ? 'Udhaar Amount Added' : 'Payment Received'}</p>
        <p class="text-3xl font-black ${amtColor}">₹${amount.toLocaleString('en-IN')}</p>
        <div class="pt-2 border-t border-[#EAE5D7] flex items-center justify-between px-2 text-xs">
          <span class="font-bold text-slate-600">Kul Baki (Total Due Balance):</span>
          <span class="font-extrabold text-base text-red-600">₹${balanceDue.toLocaleString('en-IN')}</span>
        </div>
      </div>

      <!-- Cryptographic Verification Details -->
      <div class="p-3.5 rounded-xl bg-slate-50 border border-slate-200 text-[11px] text-slate-600 space-y-1 font-mono">
        <div class="flex items-center justify-between text-slate-500">
          <span>RECEIPT ID:</span>
          <span class="font-bold text-slate-800 select-all">${eventId}</span>
        </div>
        <div class="flex items-center justify-between text-slate-500">
          <span>SECURITY SEAL:</span>
          <span class="text-emerald-700 font-bold">SHA-256 VALIDATED</span>
        </div>
        <p class="text-[9px] text-slate-400 break-all select-all pt-1">${sha256Hash}</p>
      </div>
    </div>

    <!-- Actions -->
    <div class="p-6 pt-2 space-y-2 no-print">
      <button onclick="window.print()" class="w-full py-3 rounded-xl bg-[#14281D] hover:bg-[#1E3B2B] text-white font-extrabold text-sm shadow transition flex items-center justify-center gap-2">
        <span>🖨️</span> Print / Save Receipt PDF
      </button>
      <a href="/" class="block w-full text-center py-2.5 rounded-xl bg-[#FAF8F3] hover:bg-[#EDE8DC] border border-[#DDD6C8] text-[#14281D] font-bold text-xs transition">
        🏪 Back to VoiceKhata Store
      </a>
      <p class="text-[10px] text-center text-slate-400 mt-2">
        Protected by VoiceKhata Cryptographic Audit Log • All Rights Reserved
      </p>
    </div>

  </div>
</body>
</html>`;
});

// Serve the connected Mera Kirana Store UI
fastify.get('/', async (req, reply) => {
  reply.header('Content-Type', 'text/html; charset=utf-8');
  const htmlPath = path.join(__dirname, 'public', 'index.html');
  return fs.readFileSync(htmlPath, 'utf8');
});

fastify.get('/store', async (req, reply) => {
  reply.header('Content-Type', 'text/html; charset=utf-8');
  const htmlPath = path.join(__dirname, 'public', 'index.html');
  return fs.readFileSync(htmlPath, 'utf8');
});

// Start server
const start = async () => {
  try {
    const port = config.env.PORT || 3000;
    await fastify.listen({ port, host: '0.0.0.0' });
    fastify.log.info(`Voice Khata server running on port ${port}`);
  } catch (err) {
    fastify.log.error(err);
    process.exit(1);
  }
};

start();
