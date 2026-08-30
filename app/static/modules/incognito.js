// Incognito — a per-browser switch that swaps every title, poster, requester and file name the page draws
// for a made-up one, so a real stack can be screenshotted or screen-shared without showing a library.
//
// It changes what is DRAWN and nothing else. The page keeps the real values, so the filter box and the
// palette still match what you actually own, every action body still carries the real id, and the panel's own
// log line is untouched. That also makes it a screenshot shield, not access control: whoever holds the browser
// (or the API behind it) still has the real data.
//
// Each pseudonym is a hash of the item's key into adjective + noun + number, so one title reads the same on
// every refresh and in every section and a sequence of screenshots stays coherent. Two items can land on the
// same pair — nothing depends on a pseudonym being unique, rows are still keyed by their real id.
const KEY = 'mc-incognito';
const ADJ = ['Amber', 'Brisk', 'Calm', 'Copper', 'Crimson', 'Dusty', 'Eager', 'Fern', 'Gentle', 'Glossy', 'Golden', 'Hazel',
  'Ivory', 'Jade', 'Keen', 'Lucid', 'Mellow', 'Misty', 'Noble', 'Olive', 'Pale', 'Quiet', 'Rapid', 'Rustic',
  'Sable', 'Silver', 'Slate', 'Solar', 'Steady', 'Tidy', 'Velvet', 'Warm'];
const NOUN = ['Anchor', 'Atlas', 'Beacon', 'Cabin', 'Canyon', 'Cedar', 'Comet', 'Compass', 'Delta', 'Ember', 'Falcon', 'Garden',
  'Harbour', 'Hollow', 'Island', 'Kettle', 'Lantern', 'Ledger', 'Meadow', 'Meridian', 'Orchard', 'Otter', 'Pebble', 'Quarry',
  'Ridge', 'Signal', 'Summit', 'Thicket', 'Tundra', 'Vessel', 'Willow', 'Zephyr'];

let on = false;
try { on = localStorage.getItem(KEY) === 'on'; } catch {}
const listeners = new Set();

export function isOn() { return on; }
export function setOn(v) {
  on = !!v;
  try { localStorage.setItem(KEY, on ? 'on' : 'off'); } catch {}
  for (const fn of listeners) fn(on);
}
export function toggle() { setOn(!on); return on; }
export function onChange(fn) { listeners.add(fn); }
/** Part of every keyed-render signature: flipping the switch has to invalidate every row already drawn. */
export function sig() { return on ? '·inc' : ''; }

function hash(s) {
  let h = 0x811c9dc5; s = String(s ?? '');   // FNV-1a, 32-bit: same pseudonym for the same key, forever
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 0x01000193) >>> 0;
  return h;
}
/** The pseudonym for a key — always two words from the lists above and a number, never anything of the input. */
export function alias(key) {
  const h = hash(key);
  return `${ADJ[h % ADJ.length]} ${NOUN[(h >>> 5) % NOUN.length]} ${(h >>> 10) % 90 + 10}`;
}
/** A person (requester, viewer): the same two words without the number, so it reads like a handle. */
export function person(key) {
  const h = hash('who:' + String(key ?? ''));
  return `${ADJ[h % ADJ.length]} ${NOUN[(h >>> 5) % NOUN.length]}`;
}

/** A title, a release or a file name. `key` is what keeps two views of one thing on the same pseudonym. */
export function mask(real, key) { return on ? alias(key == null || key === '' ? real : key) : real; }
export function who(real) { return on ? person(real) : real; }
export function yr(year) { return on ? null : year; }
export function poster(url) { return on ? null : url; }
/** 'S02E01 · Real title' → 'S02E01 · Quiet Otter 42'; the episode code is not a name and stays. */
export function epLabel(label, key) {
  if (!on || !label) return label;
  const m = /^(S\d+E\d+) · (.+)$/.exec(label);   // 'S02E01 · Real title'; a season pack's label carries no title
  return m ? `${m[1]} · ${alias(key || label)}` : label;
}
