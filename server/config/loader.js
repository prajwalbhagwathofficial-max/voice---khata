/**
 * Configuration Loader
 * 
 * Loads settings from multiple sources with this priority (highest wins):
 * 1. Environment variables (.env)
 * 2. Settings table in database (runtime overrides from UI)
 * 3. config/settings.json (defaults)
 * 
 * API keys are ONLY loaded from .env, never from config files.
 * The UI can update keys via a secure settings endpoint,
 * which writes them to .env or the settings DB table.
 */

import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = join(__dirname, '..', '..');

/**
 * Load and parse a JSON config file
 * @param {string} relativePath - Path relative to project root
 * @returns {object} Parsed JSON
 */
function loadJsonFile(relativePath) {
  try {
    const filePath = join(PROJECT_ROOT, relativePath);
    const content = readFileSync(filePath, 'utf-8');
    return JSON.parse(content);
  } catch (err) {
    console.error(`Failed to load config file: ${relativePath}`, err.message);
    return {};
  }
}

/**
 * Load all template files for supported languages
 * @param {string[]} languages - Array of language codes
 * @returns {object} Templates keyed by language code
 */
function loadTemplates(languages) {
  const templates = {};
  for (const lang of languages) {
    if (lang === 'auto') continue;
    templates[lang] = loadJsonFile(`config/templates/${lang}.json`);
  }
  return templates;
}

/**
 * Load all lexicon files for supported languages
 * @param {string[]} languages - Array of language codes
 * @returns {object} Lexicons keyed by language code
 */
function loadLexicons(languages) {
  const lexicons = {};
  for (const lang of languages) {
    if (lang === 'auto') continue;
    lexicons[lang] = loadJsonFile(`config/lexicon/${lang}.json`);
  }
  return lexicons;
}

/**
 * Load the full application configuration
 * Merges defaults from settings.json with environment variables.
 * 
 * @param {object} [dbOverrides] - Optional overrides from the settings DB table
 * @returns {object} Complete configuration object
 */
export function loadConfig(dbOverrides = {}) {
  // 1. Load defaults from settings.json
  const settings = loadJsonFile('config/settings.json');

  // 2. Apply database overrides (from settings table, set via UI)
  //    Deep merge: dbOverrides win over file defaults
  const mergedSettings = deepMerge(settings, dbOverrides);

  // 3. Load environment variables (secrets - never in config files)
  const env = {
    PORT: parseInt(process.env.PORT || '3000', 10),
    NODE_ENV: process.env.NODE_ENV || 'development',
    GNANI_API_KEY: process.env.GNANI_API_KEY || '',
    GNANI_STT_URL: process.env.GNANI_STT_URL || 'wss://api.vachana.ai/stt/v3/stream',
    GNANI_TTS_URL: process.env.GNANI_TTS_URL || 'wss://api.vachana.ai/api/v1/tts',
    GROQ_API_KEY: process.env.GROQ_API_KEY || '',
    GROQ_MODEL: process.env.GROQ_MODEL || 'llama-3.3-70b-versatile',
    SUPABASE_URL: process.env.SUPABASE_URL || '',
    SUPABASE_ANON_KEY: process.env.SUPABASE_ANON_KEY || '',
    SUPABASE_SERVICE_ROLE_KEY: process.env.SUPABASE_SERVICE_ROLE_KEY || '',
  };

  // 4. Load templates and lexicons
  const languages = mergedSettings.languages?.supported || ['hi', 'kn', 'en'];
  const templates = loadTemplates(languages);
  const lexicons = loadLexicons(languages);

  return {
    settings: mergedSettings,
    env,
    templates,
    lexicons,
    projectRoot: PROJECT_ROOT,
  };
}

/**
 * Deep merge two objects. Source values override target values.
 * Arrays are replaced, not concatenated.
 * 
 * @param {object} target - Base object
 * @param {object} source - Override object
 * @returns {object} Merged object
 */
function deepMerge(target, source) {
  const result = { ...target };
  for (const key of Object.keys(source)) {
    if (
      source[key] &&
      typeof source[key] === 'object' &&
      !Array.isArray(source[key]) &&
      target[key] &&
      typeof target[key] === 'object' &&
      !Array.isArray(target[key])
    ) {
      result[key] = deepMerge(target[key], source[key]);
    } else {
      result[key] = source[key];
    }
  }
  return result;
}

/**
 * Get a specific template string, with placeholder replacement
 * 
 * @param {object} templates - All loaded templates
 * @param {string} lang - Language code
 * @param {string} key - Template key
 * @param {object} [vars] - Placeholder values
 * @returns {string} Filled template string
 */
export function fillTemplate(templates, lang, key, vars = {}) {
  let template = templates[lang]?.[key] || templates['en']?.[key] || '';
  for (const [placeholder, value] of Object.entries(vars)) {
    template = template.replaceAll(`{${placeholder}}`, String(value));
  }
  return template;
}
