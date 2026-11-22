/**
 * Unit tests for Customer ID generation.
 *
 * Run:  node --test tests/customerIdGenerator.test.js
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
  generateCustomerId,
  generateUniqueCustomerId,
} from '../client/src/utils/customerIdGenerator.js';

const DEFAULT_SETTINGS = { phone_digits_in_id: 6, separator: '_' };

describe('generateCustomerId()', () => {
  it('should produce the canonical format from the spec', () => {
    const id = generateCustomerId('Pavan', 'Kengeri', '8317365720', DEFAULT_SETTINGS);
    assert.equal(id, 'pavan_kengeri_831736');
  });

  it('should lowercase everything', () => {
    const id = generateCustomerId('RAMESH', 'BANGALORE', '9876543210', DEFAULT_SETTINGS);
    assert.equal(id, 'ramesh_bangalore_987654');
  });

  it('should collapse multiple spaces into one separator', () => {
    const id = generateCustomerId('Ram  Kumar', 'JP   Nagar', '1234567890', DEFAULT_SETTINGS);
    assert.equal(id, 'ram_kumar_jp_nagar_123456');
  });

  it('should respect phone_digits_in_id setting', () => {
    const id = generateCustomerId('A', 'B', '12345678', { phone_digits_in_id: 4, separator: '_' });
    assert.equal(id, 'a_b_1234');
  });

  it('should respect custom separator', () => {
    const id = generateCustomerId('A', 'B', '12345678', { phone_digits_in_id: 6, separator: '-' });
    assert.equal(id, 'a-b-123456');
  });

  it('should strip special characters but keep Unicode letters', () => {
    const id = generateCustomerId('\u0930\u092e\u0947\u0936', '\u0915\u0947\u0902\u0917\u0947\u0930\u0940', '8317365720', DEFAULT_SETTINGS);
    assert.equal(id, '\u0930\u092e\u0947\u0936_\u0915\u0947\u0902\u0917\u0947\u0930\u0940_831736');
  });

  it('should throw if name is missing', () => {
    assert.throws(() => generateCustomerId('', 'City', '123456', DEFAULT_SETTINGS), /name/);
  });

  it('should throw if city is missing', () => {
    assert.throws(() => generateCustomerId('Name', '', '123456', DEFAULT_SETTINGS), /City/);
  });

  it('should throw if phone is missing when require_phone is default', () => {
    assert.throws(() => generateCustomerId('Name', 'City', '', DEFAULT_SETTINGS), /Phone/);
  });

  it('should use approved alternative unique identifier if phone is missing and allow_no_phone is true', () => {
    const id = generateCustomerId('Pavan', 'Kengeri', '', { ...DEFAULT_SETTINGS, allow_no_phone: true });
    assert.equal(id, 'pavan_kengeri_nophone');
  });

  it('should respect custom no_phone_identifier', () => {
    const id = generateCustomerId('Pavan', 'Kengeri', '', { ...DEFAULT_SETTINGS, allow_no_phone: true, no_phone_identifier: 'noid' });
    assert.equal(id, 'pavan_kengeri_noid');
  });
});

describe('generateUniqueCustomerId()', () => {
  it('should return base ID when no collision', async () => {
    const id = await generateUniqueCustomerId(
      'Test', 'City', '1111111111', DEFAULT_SETTINGS,
      async () => false,
    );
    assert.equal(id, 'test_city_111111');
  });

  it('should append _2 on first collision', async () => {
    const id = await generateUniqueCustomerId(
      'Test', 'City', '1111111111', DEFAULT_SETTINGS,
      async (candidate) => candidate === 'test_city_111111',
    );
    assert.equal(id, 'test_city_111111_2');
  });

  it('should append _3 when both base and _2 collide', async () => {
    const taken = new Set(['test_city_111111', 'test_city_111111_2']);
    const id = await generateUniqueCustomerId(
      'Test', 'City', '1111111111', DEFAULT_SETTINGS,
      async (candidate) => taken.has(candidate),
    );
    assert.equal(id, 'test_city_111111_3');
  });
});
