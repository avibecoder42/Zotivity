// Dubletten: dasselbe Werk mehrfach in Zotero (z. B. in Meine Bibliothek und in einer Gruppe). Je Werk wird nur eine
// Kopie gelesen und bei Fragen und im Forschungsstand verwendet. Vor einem Lauf von Hand fragt ein Dialog nach neuen
// Dubletten (beim Klick – ein wartender Lauf unterbricht nie), vorgewählt ist die Kopie aus dem Korpus; unter Index lässt
// sich jede Wahl ändern, auch für einen ganzen Korpus, unter Einstellungen → Index die Regel einstellen. Läufe ohne
// Rückfrage (Hintergrund, Zotero) wählen nach der Regel.
import { h } from '../core/vdom.js';
import { state, update, ui, run, toast, openModal, closeModal, saveSettings, loadCorpora, setBeforeIndexHandler, submitIndexRun } from '../core/store.js';
import * as api from '../core/api.js';
import { t } from '../core/i18n.js';
import { btn, card, foldCard, toggle, checkbox, select, field } from '../ui/components.js';
import { fmtInt } from '../core/util.js';

const ds = () => ui('dups', { data: undefined });

export function loadDuplicates(force = false) {
  const s = ds();
  if (!force && s.data !== undefined) return;
  if (s.data === undefined) s.data = null;
  api.get('/api/duplicates').then(r => { s.data = r; update(); }).catch(() => { s.data = { groups: [], libraries: [] }; update(); });
}

// Nach einem Lauf oder einer neuen Wahl neu laden
export function resetDuplicates() { ds().data = undefined; }

const copyId = c => `${c.lib}|${c.key}`;
// Titel nur dann eigens zeigen, wenn er sich nicht bloss in Gross-/Kleinschreibung oder Satzzeichen unterscheidet
const sameTitle = (a, b) => [a, b].map(s => (s || '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()).reduce((x, y) => x === y);
const currentSel = g => (g.mode === 'separate' ? 'separate' : copyId(g.copies.find(c => c.chosen) || g.copies[0]));
const ruleSel = g => copyId(g.copies.find(c => c.rule) || g.copies[0]);
// Im Dialog: Von Hand Gewähltes bleibt, sonst der Vorschlag (die Kopie aus dem Korpus, wenn sie gleichwertig ist)
const dialogSel = g => (g.mode === 'user' || g.mode === 'separate' ? currentSel(g) : copyId(g.copies.find(c => c.suggest) || g.copies[0]));
const textLevel = c => (c.text > 0 ? 2 : c.file ? 1 : 0);

// Auswahl → Entscheidung: wie die Regel = „rule“ (folgt dem Zustand, z. B. wenn die Kopie keinen Text hat),
// sonst „user“ (bleibt fest)
function choiceOf(g, sel) {
  if (sel === 'separate') return { work: g.work, mode: 'separate' };
  if (sel === ruleSel(g)) return { work: g.work, mode: 'rule' };
  const [lib, key] = sel.split('|');
  return { work: g.work, mode: 'user', lib, key };
}

function copyFacts(c) {
  return [c.text === 2 ? t('Text gelesen') : c.text === 1 ? t('Datei vorhanden, noch nicht gelesen') : c.file ? t('Datei vorhanden, aber kein lesbarer Text') : t('keine Datei'),
    c.pages ? t('Seitenzahlen erkannt') : null].filter(Boolean).join(' · ');
}

// Ein Werk mit seinen Kopien zur Auswahl (Bibliothek, Sammlungen, Text/Seitenzahlen) und „keine Dublette“
function groupRow(g, sel, onPick, name) {
  const option = (id, head, sub) => h('label', { class: ['dup-copy', sel === id && 'active'], key: id },
    h('input', { type: 'radio', name, checked: sel === id, onchange: () => onPick(id) }),
    h('div', { class: 'grow' }, h('div', null, head), sub && h('div', { class: 'muted small' }, sub)));
  return h('div', { class: 'dup-group', key: g.work },
    h('div', { class: 'dup-work' }, h('b', null, g.title),
      h('span', { class: 'muted small' }, ` · ${[g.creators, g.year].filter(Boolean).join(' ')}${g.doi ? ` · DOI ${g.doi}` : ''}`)),
    h('div', { class: 'dup-copies' },
      g.copies.map(c => option(copyId(c),
        [h('b', null, c.library), c.collections.length ? h('span', { class: 'muted' }, ` · ${c.collections.join(', ')}`) : null,
          c.in_corpus ? h('span', { class: 'muted' }, ` · ${t('im Korpus')}`) : null,
          c.suggest ? h('span', { class: 'dup-rule' }, ` · ${t('Vorschlag')}`) : null],
        [copyFacts(c), sameTitle(c.title, g.title) ? null : `„${c.title}“`].filter(Boolean).join(' · '))),
      option('separate', t('Keine Dublette – alle Kopien verwenden'), t('Falls es verschiedene Werke sind, z. B. eine andere Auflage.'))));
}

// ------------------------------------------------------------------ Auswahl vor einem Lauf von Hand oder für einen Korpus
// again: für einen ganzen Korpus neu wählen, auch schon entschiedene Werke; ohne anschliessenden Lauf
function openDuplicatesDialog(r, again = null) {
  return new Promise(resolve => {
    const sel = Object.fromEntries(r.groups.map(g => [g.work, dialogSel(g)]));
    const st = { never: false, busy: false };
    const libs = [...new Set(r.groups.flatMap(g => g.copies.map(c => c.lib)))];
    const libName = lib => (r.libraries.find(l => l.lib === lib) || {}).name || lib;
    // „Für alle“ wählt keine Kopie, die schlechter ist als eine andere – sonst verlöre das Werk seinen Text oder seine Datei
    const pickAll = lib => {
      for (const g of r.groups) {
        const best = Math.max(...g.copies.map(textLevel));
        const c = g.copies.find(x => x.lib === lib && textLevel(x) === best);
        if (c) sel[g.work] = copyId(c);
      }
      update();
    };
    const finish = async ok => {
      if (!ok) { closeModal(); resolve(false); return; }
      st.busy = true; update();
      try {
        await run(() => api.post('/api/duplicates/choices', { choices: r.groups.map(g => choiceOf(g, sel[g.work])) }));
        if (st.never) await saveSettings({ indexing: { ask_duplicates: false } });
      } catch { st.busy = false; update(); return; }
      resetDuplicates();
      closeModal();
      resolve(true);
    };
    const n = r.groups.length;
    openModal({
      title: again ? t('Dubletten in „{name}“ neu wählen', { name: again }) : n === 1 ? t('Vor dem Indexieren: 1 Werk liegt doppelt vor') : t('Vor dem Indexieren: {n} Werke liegen doppelt vor', { n: fmtInt(n) }),
      cls: 'modal-xl', onClose: () => resolve(false),
      body: () => h('div', { class: 'stack' },
        h('p', { class: 'muted small' }, t('Diese Werke sind in Zotero mehrfach vorhanden. Je Werk wird nur die gewählte Kopie gelesen und bei Fragen und im Forschungsstand verwendet – auch in Korpora, die nur eine andere Kopie enthalten. Vorgewählt ist die Kopie aus dem Korpus, sofern sie gleichwertig ist; ändern lässt sich die Wahl jederzeit unter Index → Dubletten.')),
        libs.length > 1 ? h('div', { class: 'row wrap small' }, h('span', { class: 'muted' }, t('Für alle:')),
          libs.map(lib => btn(t('Kopie aus {lib}', { lib: libName(lib) }), { size: 'sm', kind: 'ghost', key: lib, onClick: () => pickAll(lib) }))) : null,
        h('div', { class: 'dup-list' }, r.groups.map((g, i) => groupRow(g, sel[g.work], v => { sel[g.work] = v; update(); }, `dup-dialog-${i}`))),
        again ? null : checkbox({ checked: st.never, label: t('Künftig nicht mehr fragen und immer nach der Regel wählen'), onChange: v => { st.never = v; update(); } })),
      actions: () => [
        { label: t('Abbrechen'), onClick: () => finish(false) },
        again ? { label: t('Übernehmen'), kind: 'primary', disabled: st.busy, onClick: () => finish(true) }
          : { label: t('Übernehmen und indexieren'), kind: 'primary', icon: 'play', disabled: st.busy, onClick: () => finish(true) }],
    });
  });
}

// Vor jedem Lauf von Hand (store.submitIndexRun): neue Dubletten im Umfang abfragen. Sammlungen/Einträge (Zotero-Plugin)
// und „nicht mehr fragen“ ohne Rückfrage; ist keine Prüfung möglich, entscheidet beim Lauf die Regel.
async function askBeforeIndex(path, body) {
  const cfg = state.settings && state.settings.indexing;
  if ((cfg && cfg.ask_duplicates === false) || body.collections || body.items) return true;
  const m = /\/api\/corpora\/(\d+)\/index/.exec(path);
  const cid = m ? Number(m[1]) : body.corpus_id || null;
  const slow = setTimeout(() => toast(t('Prüfe auf Dubletten …'), 'info', 3000), 800);
  let r;
  try {
    r = await api.get(`/api/duplicates?undecided=1&sync=1${cid ? `&corpus_id=${cid}` : ''}`);
  } catch {
    return true;
  } finally {
    clearTimeout(slow);
  }
  return r.groups.length ? openDuplicatesDialog(r) : true;
}
setBeforeIndexHandler(askBeforeIndex);

// ------------------------------------------------------------------ Index → Dubletten
async function choose(g, v) {
  await run(() => api.post('/api/duplicates/choices', { choices: [choiceOf(g, v)] }));
  resetDuplicates();
  loadDuplicates();
  loadCorpora();  // die Zahlen der Korpora hängen an der gewählten Kopie
  toast(t('Gespeichert. Ist die gewählte Kopie noch nicht gelesen, liest sie der nächste Lauf ein.'), 'success', 5000);
}

// Für einen ganzen Korpus neu wählen, z. B. wenn die Kopien aus seinen Sammlungen verwendet werden sollen. Gespeichert wird
// erst mit „Übernehmen“; Abbrechen ändert nichts.
async function chooseAgain(c) {
  const r = await run(() => api.get(`/api/duplicates?corpus_id=${c.id}`));
  if (!r.groups.length) { toast(t('In diesem Korpus liegt kein Werk doppelt vor.'), 'info'); return; }
  if (!await openDuplicatesDialog(r, c.name)) return;
  loadDuplicates();
  loadCorpora();
  toast(t('Gespeichert. Eine neu gewählte Kopie liest der nächste Lauf ein.'), 'success', 9000,
    { label: t('Jetzt indexieren'), onClick: () => submitIndexRun(`/api/corpora/${c.id}/index`, {}) });
}

function chooseAgainRow() {
  const s = ds();
  const corpora = state.corpora || [];
  const shown = corpora.find(c => c.id === s.againId) || corpora.find(c => c.id === ui('index', {}).corpus) || corpora[0];
  if (!shown) return null;
  return h('div', { class: 'row wrap' }, h('span', { class: 'small' }, t('Für einen Korpus neu wählen:')),
    select({ value: shown.id, label: t('Korpus'), onChange: v => { s.againId = Number(v); update(); }, options: corpora.map(c => ({ value: c.id, label: c.name })) }),
    btn(t('Neu wählen …'), { size: 'sm', onClick: () => chooseAgain(shown) }));
}

export function duplicatesCard() {
  loadDuplicates();
  const d = ds().data;
  if (!d || !d.groups.length) return null;
  const groups = d.groups;
  const count = mode => groups.filter(g => g.mode === mode).length;
  const auto = groups.length - count('user') - count('separate');
  const summary = [
    groups.length === 1 ? t('1 Werk liegt doppelt vor') : t('{n} Werke liegen doppelt vor', { n: fmtInt(groups.length) }),
    count('user') ? t('{n} von dir gewählt', { n: fmtInt(count('user')) }) : null,
    auto ? t('{n} nach der Regel', { n: fmtInt(auto) }) : null,
    count('separate') ? t('{n} als keine Dublette', { n: fmtInt(count('separate')) }) : null,
  ].filter(Boolean).join(' · ');
  return foldCard('dups', t('Dubletten ({n})', { n: fmtInt(groups.length) }), 'copy', summary, () => h('div', { class: 'stack' },
    h('p', { class: 'muted small' }, t('Je Werk wird nur die gewählte Kopie gelesen und bei Fragen und im Forschungsstand verwendet – auch in Korpora, die nur eine andere Kopie enthalten. Eine andere Wahl liest der nächste Lauf ein; bis dahin bleibt die bisherige Kopie in Gebrauch.')),
    chooseAgainRow(),
    h('div', { class: 'dup-list dup-list-card' }, groups.map((g, i) => groupRow(g, currentSel(g), v => choose(g, v), `dup-card-${i}`)))),
  { class: 'span-2' });
}

// ------------------------------------------------------------------ Einstellungen → Index
export function duplicatesSettingsCard() {
  const cfg = state.settings && state.settings.indexing;
  if (!cfg) return null;
  loadDuplicates();
  const libs = (ds().data && ds().data.libraries && ds().data.libraries.length) ? ds().data.libraries : [{ lib: 'users/0', name: t('Meine Bibliothek') }];
  return card(t('Dubletten'), 'copy', h('div', { class: 'stack' },
    h('p', { class: 'muted small' }, t('Dasselbe Werk mehrfach in Zotero, z. B. in Meine Bibliothek und in einer Gruppe: Je Werk wird nur eine Kopie gelesen und verwendet. Erkannt an der DOI, sonst an Titel, Erstautor/in und Jahr.')),
    toggle({ checked: cfg.ask_duplicates, label: t('Vor dem Indexieren fragen, welche Kopie verwendet wird'),
      onChange: v => saveSettings({ indexing: { ask_duplicates: v } }) }),
    field(t('Sonst bevorzugen'), select({ value: cfg.duplicate_library, options: libs.map(l => ({ value: l.lib, label: l.name })),
      onChange: async v => { await saveSettings({ indexing: { duplicate_library: v } }); resetDuplicates(); loadCorpora(); update(); } }),
    { help: t('Die Regel wählt zuerst die Kopie mit lesbarem Text, dann eine mit Datei, unter gleichwertigen die aus dem Korpus, dann die mit gelesenem Text und erkannten Seitenzahlen, dann die aus dieser Bibliothek.') })));
}
