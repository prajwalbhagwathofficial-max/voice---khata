/**
 * Unit tests for the encryption module.
 *
 * Run:  node --test tests/crypto.test.js
 */

import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';

import {
  setupEncryption,
  initEncryption,
  verifyPin,
  encrypt,
  decrypt,
  isEncryptionReady,
  clearEncryption,
} from '../client/src/crypto/encryption.js';

describe('Encryption Service', () => {
  const TEST_PIN = '1234';
  const WRONG_PIN = '9999';
  let salt, testCipher;

  before(() => {
    clearEncryption();
  });

  after(() => {
    clearEncryption();
  });

  describe('setupEncryption()', () => {
    it('should generate salt and testCipher', async () => {
      const result = await setupEncryption(TEST_PIN);
      salt = result.salt;
      testCipher = result.testCipher;

      assert.ok(salt, 'salt should be a non-empty string');
      assert.ok(testCipher, 'testCipher should be a non-empty string');
      assert.ok(isEncryptionReady(), 'key should be in memory after setup');
    });
  });

  describe('encrypt() / decrypt()', () => {
    it('should round-trip a plain string', async () => {
      const plaintext = '8317365720';
      const ciphertext = await encrypt(plaintext);

      assert.notEqual(ciphertext, plaintext, 'ciphertext must differ from plaintext');
      assert.ok(ciphertext.length > 0, 'ciphertext should not be empty');

      const decrypted = await decrypt(ciphertext);
      assert.equal(decrypted, plaintext);
    });

    it('should produce different ciphertexts for the same input (random IV)', async () => {
      const a = await encrypt('hello');
      const b = await encrypt('hello');
      assert.notEqual(a, b, 'Each encryption should use a unique IV');
    });

    it('should handle empty string gracefully', async () => {
      const result = await encrypt('');
      assert.equal(result, '');
      const dec = await decrypt('');
      assert.equal(dec, '');
    });

    it('should handle Unicode (Hindi / Kannada)', async () => {
      const hindi = '\u0930\u092e\u0947\u0936 \u0915\u093e \u092b\u093c\u094b\u0928 \u0928\u0902\u092c\u0930';
      const enc = await encrypt(hindi);
      const dec = await decrypt(enc);
      assert.equal(dec, hindi);
    });
  });

  describe('verifyPin()', () => {
    it('should return true for the correct PIN', async () => {
      const ok = await verifyPin(TEST_PIN, salt, testCipher);
      assert.equal(ok, true);
    });

    it('should return false for a wrong PIN', async () => {
      const ok = await verifyPin(WRONG_PIN, salt, testCipher);
      assert.equal(ok, false);
    });
  });

  describe('initEncryption()', () => {
    it('should restore the key from salt + PIN', async () => {
      clearEncryption();
      assert.equal(isEncryptionReady(), false);

      await initEncryption(TEST_PIN, salt);
      assert.equal(isEncryptionReady(), true);

      // Verify we can still encrypt/decrypt
      const plaintext = 'test data 42';
      const enc = await encrypt(plaintext);
      const dec = await decrypt(enc);
      assert.equal(dec, plaintext);
    });
  });

  describe('clearEncryption()', () => {
    it('should wipe the in-memory key', () => {
      clearEncryption();
      assert.equal(isEncryptionReady(), false);
    });

    it('should throw when encrypting without a key', async () => {
      await assert.rejects(() => encrypt('boom'), /not initialised/);
    });
  });
});
