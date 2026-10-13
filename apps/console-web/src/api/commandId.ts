// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// Command ids are minted by the client and are random from the FIRST character: nothing in them counts, orders or
// names anything, so one console cannot collide with another and an id carries no information about this machine.

const ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789';

function randomBytes(count: number): Uint8Array {
  const out = new Uint8Array(count);
  const source = globalThis.crypto;
  if (source !== undefined && typeof source.getRandomValues === 'function') {
    source.getRandomValues(out);
    return out;
  }
  // No platform source: fail loudly rather than mint a predictable id.
  throw new Error('no cryptographic random source is available for minting a command id');
}

export function mintCommandId(length = 24): string {
  const bytes = randomBytes(length);
  let out = '';
  for (const byte of bytes) out += ALPHABET[byte % ALPHABET.length];
  return out;
}
