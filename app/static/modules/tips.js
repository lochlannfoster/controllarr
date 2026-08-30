// Tooltips that work on a phone. Desktop hovers show the native `title`; touch has no hover, so:
//  - a long-press (450 ms) on any control with a title shows the same text in a popover, and letting go does not click;
//  - the "?" button in the header turns on help mode: every tap shows the control's tip instead of acting.
// One popover element (role=tooltip), dismissed by the next tap, Escape, or scrolling.
import { h } from './dom.js';

let pop = null, helpOn = false, pressTimer = null, pressed = null, swallow = false;
const tipOf = target => { const el = target.closest && target.closest('[title], [data-tip]'); if (!el) return null; const text = el.getAttribute('data-tip') || el.getAttribute('title'); return text ? { el, text } : null; };

export function showTip(el, text) {
  hideTip();
  pop = h('div', { class: 'tip', role: 'tooltip', id: 'tip-pop' }, text);
  document.body.append(pop);
  const r = el.getBoundingClientRect(), pw = pop.offsetWidth, ph = pop.offsetHeight;
  let top = r.top - ph - 8; const left = Math.min(Math.max(8, r.left + r.width / 2 - pw / 2), innerWidth - pw - 8);
  if (top < 8) { top = r.bottom + 8; pop.classList.add('tip-below'); }
  pop.style.top = top + 'px'; pop.style.left = left + 'px';
  el.setAttribute('aria-describedby', 'tip-pop'); pop._for = el;
  return pop;
}
export function hideTip() { if (pop) { pop._for?.removeAttribute('aria-describedby'); pop.remove(); pop = null; } }
export function helpMode() { return helpOn; }

export function initTips(helpBtn) {
  const cancel = () => { clearTimeout(pressTimer); pressTimer = null; };
  document.addEventListener('pointerdown', e => {
    if (e.pointerType === 'mouse') return;
    const t = tipOf(e.target); pressed = t; cancel(); if (!t) return;
    pressTimer = setTimeout(() => { pressTimer = null; showTip(t.el, t.text); swallow = true; }, 450);
  }, { passive: true });
  document.addEventListener('pointermove', () => { if (pressTimer) cancel(); }, { passive: true });
  document.addEventListener('pointerup', cancel, { passive: true });
  document.addEventListener('pointercancel', cancel, { passive: true });
  document.addEventListener('contextmenu', e => { if (pressed && (swallow || pressTimer)) e.preventDefault(); });
  // capture phase: a click that ends a long-press is swallowed; in help mode a tap explains instead of acting
  document.addEventListener('click', e => {
    if (swallow) { swallow = false; e.preventDefault(); e.stopPropagation(); return; }
    if (pop && !pop.contains(e.target)) hideTip();
    if (!helpOn || (helpBtn && helpBtn.contains(e.target))) return;
    const t = tipOf(e.target); if (!t) return;
    e.preventDefault(); e.stopPropagation(); showTip(t.el, t.text);
  }, true);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hideTip(); });
  document.addEventListener('scroll', hideTip, { passive: true, capture: true });
  if (helpBtn) helpBtn.addEventListener('click', () => {
    helpOn = !helpOn; helpBtn.setAttribute('aria-pressed', String(helpOn)); document.documentElement.classList.toggle('help-mode', helpOn); hideTip();
    if (helpOn) showTip(helpBtn, 'Help mode: tap any control to read what it does instead of running it. Tap ? again to leave.');
  });
}
