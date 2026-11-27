// Fragen an die Korpora des Projekts: Antwort mit anklickbaren Belegen, Textstellen eingeklappt darunter.
// Mehrere Korpora gleichzeitig wählbar; Antworten und Textstellen lassen sich kopieren und exportieren.
import { h } from '../core/vdom.js';
import { state, update, ui, toast, confirmDialog, providerLabel, loadStatus, loadProjects, loadPresets, loadSettings, project, projectCorpora, run } from '../core/store.js';
import * as api from '../core/api.js';
import { t } from '../core/i18n.js';
import { icon } from '../ui/icons.js';
import { btn, iconBtn, segmented, notice, spinner, numberInput, toggle, field, emptyState, multiSelect, paneProps, splitter } from '../ui/components.js';
import { markdown } from '../core/markdown.js';
import { fmtAgo, fmtInt, fmtNum, nTexts, nIndexed } from '../core/util.js';
import { navigate, viewHeader, openSourcePopover } from './shell.js';
import { exportMenu, copyTarget } from './exports.js';
import { coverageNotice } from './embedmodels.js';

const examples = () => [
  t('Was schreiben meine Quellen über die Nutzung von Chatbots durch Jugendliche?'),
  t('Wo argumentiert jemand gegen den Einsatz von KI in der Sozialen Arbeit?'),
  t('Welche Methoden werden in den Studien verwendet, und mit welchen Stichproben?'),
];

function st() {
  // corpora: gewählte Korpora des Projekts (leer = alle); scope: Aufruf aus Zotero für Sammlung/Einträge
  return ui('ask', { chatId: null, messages: [], chats: null, chatsFor: null, busy: false, corpora: [], mode: 'answer', showOptions: false,
    overrides: {}, overridesBase: null, draft: '', stick: true, controller: null, openChat: null, scope: null,
    scopeMode: 'auto', plan: null, planKey: '', planTimer: null, qtype: 'auto', lastQtype: '' });
}

// Sitzungswerte der Suchoptionen gelten, bis sich die Experten-Werte oder das Preset ändern (zuletzt Eingestelltes gilt)
function syncOverrides(s) {
  if (!state.settings) return;
  const base = JSON.stringify(state.settings.retrieval);
  if (s.overridesBase !== null && s.overridesBase !== base) s.overrides = {};
  s.overridesBase = base;
}

// Suchplan vor dem Senden (ganze Texte oder Textstellen, Textmenge, Werte) – neu, sobald sich etwas ändert
function planBody(s) {
  return { filters: s.scope ? s.scope.filters : { corpus_ids: selectedCorpora() }, scope_mode: s.scopeMode,
    retrieval: s.overrides, search_only: s.mode === 'search' };
}

function ensurePlan(s) {
  if (!s.scope && !projectCorpora().length) { s.plan = null; s.planKey = ''; return; }
  const body = planBody(s);
  const llm = state.status && state.status.llm;
  const key = JSON.stringify([body, state.settings && state.settings.retrieval, llm && llm.provider, state.catalogGen]);
  if (key === s.planKey) return;
  s.planKey = key;
  clearTimeout(s.planTimer);
  s.planTimer = setTimeout(async () => {
    try {
      const plan = await api.post('/api/chat/plan', body);
      if (st().planKey === key) { st().plan = plan; update(); }
    } catch { /* ohne Planzeile weiter */ }
  }, 120);
}

// Tokens rund: 70 000, 2,1 Mio.
export const fmtTokens = n => n >= 1e6 ? t('{n} Mio.', { n: fmtNum(n / 1e6, 1) })
  : fmtInt(n >= 10000 ? Math.round(n / 1000) * 1000 : n >= 1000 ? Math.round(n / 100) * 100 : n);

function stagesReason(p) {
  if (p.stages_reason === 'too_many_texts') return t('In Etappen lassen sich höchstens 5 Texte lesen.');
  if (p.stages_reason === 'too_long') return t('Zu lang für Etappen: etwa {n} Etappen, höchstens 60.', { n: fmtInt(p.stages) });
  return reasonText({ ...p, reason: p.stages_reason });
}

const modelOf = p => p.stage_model === 'main' ? t('Hauptmodell') : t('Hilfsmodell');

function reasonText(p) {
  if (p.reason === 'too_large') return t('Zu viel Text: rund {n} Tokens, Grenze {l} (Einstellungen → Experten)', { n: fmtTokens(p.tokens), l: fmtTokens(p.limit) });
  if (p.reason === 'empty') return t('Im Suchbereich ist noch kein Text gelesen.');
  if (p.reason === 'off') return t('Ganze Texte sind für dieses Sprachmodell ausgeschaltet (Einstellungen → Experten).');
  if (p.reason === 'search_only') return t('„Nur Textstellen“ arbeitet ohne Sprachmodell.');
  return '';
}

async function loadChats() {
  const s = st();
  s.chatsFor = state.projectId;
  try { s.chats = (await api.get(`/api/chats?project=${state.projectId}`)).chats; } catch { s.chats = []; }
  update();
}

function selectedCorpora() {
  const all = projectCorpora().map(c => c.id);
  const chosen = st().corpora.filter(id => all.includes(id));
  return chosen.length ? chosen : all;
}

async function openChat(id) {
  const s = st();
  if (s.busy) return;
  const { messages } = await api.get(`/api/chats/${id}`);
  s.chatId = id;
  s.messages = messages.map(m => m.role === 'user' ? { role: 'user', content: m.content }
    : { id: m.id, role: 'assistant', content: m.content, sources: m.sources || [], queries: (m.meta || {}).queries || [],
      errors: (m.meta || {}).error ? [{ message: m.meta.error }] : [], warnings: [], meta: m.meta || {}, done: true });
  const last = messages.filter(m => m.role === 'user').pop();
  const f = last && last.meta && last.meta.filters;
  if (f && f.corpus_ids) s.corpora = f.corpus_ids;
  else if (f && f.corpus_id) s.corpora = [f.corpus_id];
  if (last && last.meta && last.meta.scope) s.scopeMode = last.meta.scope;  // ein Chat mit ganzen Texten bleibt dabei
  s.stick = true;
  update();
}

function newChat() {
  const s = st();
  if (s.busy) return;
  s.chatId = null; s.messages = []; s.draft = '';
  update();
  requestAnimationFrame(() => { const el = document.getElementById('ask-input'); if (el) el.focus(); });
}

async function send() {
  const s = st();
  const question = s.draft.trim();
  if (!question || s.busy) return;
  s.draft = '';
  const input = document.getElementById('ask-input');
  if (input) input.value = '';  // Feld hat den Fokus – direkt leeren
  s.busy = true;
  s.stick = true;
  s.messages.push({ role: 'user', content: question });
  const msg = { role: 'assistant', content: '', sources: [], queries: [], warnings: [], errors: [], status: t('Starte …'), meta: {}, done: false };
  s.messages.push(msg);
  update();
  const filters = s.scope ? s.scope.filters : { corpus_ids: selectedCorpora() };
  s.controller = new AbortController();
  try {
    await api.stream('/api/chat', { question, chat_id: s.chatId, filters, retrieval: s.overrides, search_only: s.mode === 'search',
      scope_mode: s.scopeMode, qtype: s.qtype, project_id: state.projectId }, ev => {
      switch (ev.type) {
        case 'chat': if (s.chatId !== ev.chat_id) loadProjects(); s.chatId = ev.chat_id; break;  // neuer Chat: Projektübersicht nachführen
        case 'status': msg.status = t(ev.text); break;
        case 'queries': msg.queries = ev.queries; msg.missing = ev.missing; break;
        case 'plan': msg.plan = ev.plan; break;
        case 'qtype': msg.qtype = ev.qtype; s.lastQtype = ev.detected || ''; break;
        case 'sources': msg.sources = ev.sources; break;
        case 'delta': msg.status = ''; msg.content += ev.text; break;
        case 'rewrite': msg.content = ev.text; break;  // ganze Texte: Belege [1, S. 64] → [n]
        case 'warning': msg.warnings.push(ev.text); break;
        case 'error': msg.status = ''; msg.errors.push({ message: ev.message, kind: ev.kind }); break;
        case 'done': msg.status = ''; msg.meta = { model: ev.model, usage: ev.usage, basis: ev.basis, stages: ev.stages }; msg.id = ev.message_id; break;
        default: break;
      }
      update();
    }, s.controller.signal);
  } catch (err) {
    msg.errors.push({ message: err.message });
  }
  msg.status = '';
  msg.done = true;
  s.busy = false;
  s.controller = null;
  update();
  loadChats();
  loadStatus();
  loadProjects();
}

function stop() {
  const s = st();
  if (s.controller) s.controller.abort();
}

// ------------------------------------------------------------------ Darstellung
function sourcesBlock(m) {
  if (!m.sources.length) return null;
  const docs = new Set(m.sources.map(x => x.item_key)).size;
  return h('details', { class: 'sources', key: 'src' },
    h('summary', null, icon('quote', 14), docs === 1 ? t('{n} Textstellen aus einem Dokument', { n: m.sources.length })
      : t('{n} Textstellen aus {d} Dokumenten', { n: m.sources.length, d: docs }),
      m.id ? h('span', { class: 'summary-actions', onclick: e => e.preventDefault() },
        iconBtn('copy', t('Textstellen kopieren'), e => { e.stopPropagation(); copyTarget({ kind: 'sources', id: m.id }); }),
        iconBtn('download', t('Textstellen exportieren …'), e => { e.stopPropagation(); exportMenu(e, { kind: 'sources', id: m.id }, t('Textstellen')); })) : null),
    h('div', { class: 'source-list' }, m.sources.map(x => h('div', { class: 'source', key: x.chunk_id, id: `src-${x.chunk_id}` },
      h('div', { class: 'source-head' }, h('span', { class: 'cite static' }, x.n),
        h('div', { class: 'source-title' }, h('b', null, x.citation), ' · ', x.title),
        h('span', { class: 'muted small' }, x.kind === 'abstract' ? 'Abstract' : x.kind === 'figure' ? `${t('Abbildung (maschinell beschrieben)')}${x.page_label ? `, ${t('S.')} ${x.page_label}` : ''}` : x.page_label ? `${t('S.')} ${x.page_label}` : '')),
      h('div', { class: ['source-text', !x.text && 'muted'] }, x.text || (x.whole ? t('Diese Seite liess sich im Text nicht finden.') : '')),
      h('div', { class: 'source-links' },
        x.zotero_pdf && h('a', { href: x.zotero_pdf }, icon('pdf', 13), ' ', x.page_label ? t('PDF öffnen (S. {p})', { p: x.page_label }) : t('PDF öffnen')),
        h('a', { href: x.zotero_select }, icon('zotero', 13), ' ', t('In Zotero zeigen')),
        h('span', { class: 'spacer' }),
        relevanceMeter(x.relevance))))));
}

// Relevanz einer Textstelle: fünf Stufen, gemessen am besten Treffer der Frage (keine Wahrscheinlichkeit)
function relevanceMeter(r) {
  if (!r) return null;
  const num = v => fmtNum(v, 3);
  const label = t(r.label);
  const lines = [t('Relevanz: {l}', { l: label }), t('Rang {r} von {n}', { r: r.rank, n: r.of })];
  if (r.basis === 'rerank') lines.push(t('Bewertung der Neusortierung: {p} %', { p: Math.round(r.rerank * 100) }));
  if (r.vector != null) lines.push(t('Bedeutungsähnlichkeit {v}', { v: num(r.vector) }) + (r.best_vector != null ? ` (${t('bester Treffer {v}', { v: num(r.best_vector) })})` : ''));
  lines.push(r.keyword ? t('Stichworttreffer: ja') : t('Stichworttreffer: nein'));
  if (r.basis !== 'rerank') lines.push(t('Gemessen am besten Treffer dieser Frage – keine Wahrscheinlichkeit.'));
  return h('span', { class: ['relevance', `rel-${r.level}`], title: lines.join('\n') },
    h('span', { class: 'rel-bars', 'aria-hidden': 'true' }, [1, 2, 3, 4, 5].map(i => h('i', { key: i, class: i <= r.level ? 'on' : null }))),
    h('span', null, r.basis === 'rerank' ? `${Math.round(r.rerank * 100)} %` : label));
}

function errorBlock(e) {
  const setup = e.kind === 'setup' || e.kind === 'auth' || /API-Schlüssel|API key/.test(e.message);
  return notice('error', e.message, setup ? h('div', { class: 'notice-actions' },
    btn(t('Zu den Sprachmodellen'), { size: 'sm', kind: 'primary', onClick: () => { ui('settings', {}).tab = 'models'; navigate('settings'); } })) : null);
}

function assistantMessage(m, i) {
  const usage = m.meta && m.meta.usage;
  return h('div', { class: 'msg assistant', key: 'm' + i },
    m.queries && m.queries.length > 1 ? h('details', { class: 'queries' },
      h('summary', null, icon('search', 13), t('{n} Suchanfragen', { n: m.queries.length })),
      h('div', { class: 'query-chips' }, m.queries.map((q, j) => h('span', { key: j }, q))),
      m.missing && h('div', { class: 'muted small' }, t('Nachrecherche: '), m.missing)) : null,
    m.status && h('div', { class: 'statusline' }, spinner(13), h('span', null, m.status)),
    m.content && h('div', { class: 'answer-wrap' },
      h('div', { class: 'answer prose', html: markdown(m.content, { cites: true }),
        onclick: e => {
          const b = e.target.closest('.cite');
          if (!b) return;
          const src = m.sources.find(x => x.n === Number(b.dataset.n));
          if (src) openSourcePopover(b, src);
        } }),
      m.done && m.id ? h('div', { class: 'answer-tools' },
        iconBtn('copy', t('Antwort mit Quellen kopieren'), () => copyTarget({ kind: 'answer', id: m.id })),
        iconBtn('download', t('Antwort exportieren (Word, LaTeX, Markdown) …'), e => exportMenu(e, { kind: 'answer', id: m.id }, t('Antwort')))) : null),
    m.warnings.map((w, j) => h('div', { key: 'w' + j }, notice('warning', w, null, { dismiss: `chatwarn:${m.id || 'neu'}:${w}`, session: true }))),
    m.errors.map((e, j) => h('div', { key: 'e' + j }, errorBlock(e))),
    sourcesBlock(m),
    basisLine(m),
    notesBlock(m),
    m.done && (m.meta.model || (usage && usage.requests)) ? h('div', { class: 'msg-meta' },
      m.meta.model && h('span', null, icon('ai', 12), ' ', m.meta.model),
      usage && usage.requests ? h('span', null, t('{a} Input-Token · {b} Output-Token', { a: fmtInt(usage.input_tokens), b: fmtInt(usage.output_tokens) })) : null) : null);
}

// Grundlage einer Antwort: wie viel Text das Sprachmodell hatte
function basisLine(m) {
  const b = m.meta && m.meta.basis;
  if (!b || !m.done) return null;
  if (b.mode === 'whole') {
    return h('div', { class: 'basis-line' }, icon('file', 12),
      h('span', null, b.texts.length === 1 ? t('Grundlage: ganzer Text') : t('Grundlage: {n} ganze Texte', { n: b.texts.length }), ' – ',
        b.texts.map((x, i) => [i ? '; ' : '', h('a', { key: x.item_key, href: x.zotero_select, title: x.title }, x.citation)]),
        ` · ${t('rund {n} Tokens', { n: fmtTokens(b.tokens) })}`));
  }
  if (b.mode === 'stages') {
    return h('div', { class: 'basis-line' }, icon('layers', 12),
      h('span', null, t('Grundlage: {n} in {s} Etappen gelesen, {k} mit Notizen', { n: b.texts.length === 1 ? t('ganzer Text') : t('{n} ganze Texte', { n: b.texts.length }), s: b.stages, k: b.with_notes }), ' – ',
        b.texts.map((x, i) => [i ? '; ' : '', h('a', { key: x.item_key, href: x.zotero_select, title: x.title }, x.citation)]),
        ` · ${t('rund {n} Tokens', { n: fmtTokens(b.tokens) })}`));
  }
  const kind = b.qtype && b.qtype !== 'detail' ? ` · ${t('Frageart: {q}', { q: QTYPE_LABELS()[b.qtype] || b.qtype })}` : '';
  if (b.mode === 'cards') {
    const text = b.passages
      ? t('Grundlage: Karten von {c} von {n} Texten und {p} Textstellen aus {d} Texten', { c: fmtInt(b.cards), n: fmtInt(b.scope_docs), p: fmtInt(b.passages), d: fmtInt(b.passage_docs) })
      : t('Grundlage: Karten von {c} von {n} Texten (Abstract, Angaben, Auswertungsblatt)', { c: fmtInt(b.cards), n: fmtInt(b.scope_docs) });
    return h('div', { class: 'basis-line' }, icon('layers', 12), h('span', null, text + kind));
  }
  return h('div', { class: 'basis-line' }, icon('quote', 12),
    h('span', null, t('Grundlage: {p} Textstellen aus {d} von {n} Texten', { p: fmtInt(b.passages), d: fmtInt(b.docs), n: fmtInt(b.scope_docs) }) + kind));
}

// Notizen je Etappe (Lesen in Etappen), eingeklappt unter der Antwort
function notesBlock(m) {
  const list = (m.meta && m.meta.stages) || [];
  if (!m.done || !list.length) return null;
  const withNotes = list.filter(x => x.notes && !/^nichts zur frage/i.test(x.notes.trim()));
  return h('details', { class: 'queries stage-notes', key: 'notes' },
    h('summary', null, icon('layers', 13), t('Notizen der Etappen ({k} von {n})', { k: withNotes.length, n: list.length })),
    h('div', { class: 'stage-list' }, list.map(x => h('div', { key: x.index, class: 'stage-item' },
      h('div', { class: 'muted small' }, t('Etappe {i}', { i: x.index }), x.pages ? ` · ${x.pages}` : '', x.headings && x.headings.length ? ` · ${x.headings.join(' · ')}` : ''),
      h('div', { class: 'stage-notes-text' }, x.notes || '–')))));
}

// Frageart: das Hilfsmodell ordnet die Frage ein (mit der Mehrfachsuche); hier lässt sie sich festlegen
const QTYPE_LABELS = () => ({ auto: t('Automatisch'), detail: t('Detail'), overview: t('Überblick'), evidence: t('Belege') });

function qtypeSelect(s) {
  const labels = QTYPE_LABELS();
  return h('label', { class: 'plan-qtype', title: t('Überblicksfragen erhalten von jedem Text eine kurze Karte und dazu die passendsten Textstellen. Vergleiche von Begriffen oder Positionen gelten als Detailfragen. Automatisch: das Hilfsmodell ordnet die Frage ein (mit der Mehrfachsuche).') },
    t('Frageart'),
    h('select', { class: 'select select-sm', value: s.qtype, onchange: e => { s.qtype = e.target.value; update(); } },
      Object.entries(labels).map(([v, l]) => h('option', { key: v, value: v, selected: s.qtype === v },
        v === 'auto' && s.lastQtype ? `${l} (${t('zuletzt: {q}', { q: labels[s.lastQtype] || s.lastQtype })})` : l))));
}

function chatList() {
  const s = st();
  if (s.chats === null || s.chatsFor !== state.projectId) { if (s.chatsFor !== state.projectId) { s.chats = null; loadChats(); } return h('aside', { class: 'side-list', ...paneProps('ask', 260) }, spinner(16)); }
  return h('aside', { class: 'side-list', ...paneProps('ask', 260) },
    btn(t('Neue Frage'), { kind: 'primary', icon: 'plus', onClick: newChat }),
    h('div', { class: 'side-items' }, s.chats.length ? s.chats.map(c => h('div', {
      key: c.id, class: ['side-item', c.id === s.chatId && 'active'], role: 'button', tabindex: 0, onclick: () => openChat(c.id),
    }, h('div', { class: 'side-item-text', title: c.title }, h('div', { class: 'side-item-title' }, c.title), h('div', { class: 'side-item-sub' }, fmtAgo(c.updated_at))),
    iconBtn('trash', t('Löschen'), async e => {
      e.stopPropagation();
      if (!await confirmDialog(t('Frage löschen?'), t('„{name}“ wird aus dem Verlauf entfernt.', { name: c.title }), { ok: t('Löschen'), danger: true })) return;
      await api.del(`/api/chats/${c.id}`);
      if (c.id === s.chatId) newChat();
      loadChats(); loadProjects();
    }))) : h('p', { class: 'muted small pad' }, t('Noch keine Fragen in diesem Projekt.'))),
    splitter('ask', { def: 260, max: 520 }));
}

// Preset-Umschalter: wendet die Such- und Antwortwerte für alle weiteren Fragen an
function presetSwitch() {
  const p = state.presets;
  if (!p) return null;
  const main = ['schnell', 'ausgewogen', 'gruendlich'];
  const byId = Object.fromEntries(p.presets.map(x => [x.id, x]));
  const ids = main.includes(p.active) || !byId[p.active] ? main : [...main, p.active];
  return h('div', { class: 'preset-switch', title: p.changed.length ? t('Werte gegenüber „{name}“ geändert ({n})', { name: t((byId[p.active] || {}).label || ''), n: p.changed.length }) : t('Such-Preset') },
    segmented({ size: 'sm', value: p.active, onChange: async id => {
      await run(() => api.post('/api/presets/apply', { id }));
      await Promise.all([loadPresets(), loadSettings()]);
      toast(`${t('Preset')} „${t(byId[id].label)}“: ${t(byId[id].description)}`, 'info', 4000);
    }, options: ids.filter(id => byId[id]).map(id => ({ value: id, label: t(byId[id].label), title: t(byId[id].description) })) }),
    p.changed.length ? h('span', { class: 'preset-changed', title: t('Werte weichen vom Preset ab – Einstellungen › Experten') }, '•') : null);
}

// Umfang: ganze Texte (wenn sie für das Sprachmodell passen) oder Textstellen
function scopeSwitch() {
  const s = st();
  const p = s.plan;
  const blocked = p && !p.whole_ok && p.reason !== 'search_only';
  return h('div', { class: 'ask-scope-mode' }, h('span', { class: 'muted small' }, t('Umfang')),
    segmented({ size: 'sm', value: s.scopeMode, onChange: v => { s.scopeMode = v; update(); }, options: [
      { value: 'auto', label: t('Automatisch'), title: t('Ganze Texte, wenn sie für das Sprachmodell passen, sonst die passendsten Textstellen') },
      { value: 'passages', label: t('Textstellen'), title: t('Immer die passendsten Textstellen, mit den Werten aus Preset und Suchoptionen') },
      { value: 'whole', label: t('Ganze Texte'), disabled: blocked, title: blocked ? reasonText(p) : t('Das Sprachmodell erhält die ganzen Texte mit Seitenangaben') },
      { value: 'stages', label: t('In Etappen'), disabled: p && !p.stages_ok && p.stages_reason !== 'search_only',
        title: p && !p.stages_ok ? stagesReason(p) : t('Für Texte über der Grenze: kapitelweise lesen, je Etappe Notizen, dann die Antwort aus den Notizen. Langsamer und mit mehr Aufrufen, aber vollständig.') }] }));
}

// Planzeile über dem Eingabefeld: was beim Senden geschieht, dazu die Sitzungswerte der Suchoptionen
const SESSION_LABELS = () => ({ top_k: t('Textstellen'), max_chunks_per_doc: t('Max. pro Text'), research_rounds: t('Recherche-Runden'), multi_query: t('Mehrfachsuche') });
const AUTO_LABELS = () => ({ top_k: t('Textstellen'), max_chunks_per_doc: t('Max. pro Text'), mmr_lambda: t('Relevanz vs. Vielfalt'),
  neighbor_chunks: t('Nachbarabschnitte'), candidate_k: t('Kandidaten je Suchverfahren') });

// Was „Automatisch“ bei dieser Zahl von Texten angepasst hat (Tooltip der Planzeile)
function autoTitle(p) {
  const lines = Object.entries(p.auto || {}).map(([k, v]) => `${AUTO_LABELS()[k] || k}: ${v >= 50 && k === 'max_chunks_per_doc' ? t('unbegrenzt') : v}`);
  if (p.fixed && p.fixed.length) lines.push(t('Fest unter Experten: {f}', { f: p.fixed.map(k => AUTO_LABELS()[k] || k).join(', ') }));
  return [t('Automatisch angepasst an {n} Texte im Suchbereich', { n: fmtInt(p.n_docs) }), ...lines].join('\n');
}

function planLine() {
  const s = st();
  const p = s.plan;
  if (!p) return null;
  const v = p.values;
  const texts = p.n_docs === 1 ? t('1 Text') : t('{n} Texte', { n: fmtInt(p.n_docs) });
  const main = p.mode === 'stages'
    ? [icon('layers', 13), h('b', null, t('In Etappen')), h('span', null, `${texts} · ${t('rund {n} Tokens', { n: fmtTokens(p.tokens) })} · ${t('etwa {n} Etappen, Notizen mit dem {m}', { n: fmtInt(p.stages), m: modelOf(p) })}`)]
    : p.mode === 'whole'
    ? [icon('file', 13), h('b', null, t('Ganze Texte')), h('span', null, `${texts} · ${t('rund {n} Tokens', { n: fmtTokens(p.tokens) })}`)]
    : [icon('quote', 13), h('b', null, s.mode === 'search' ? t('Nur Textstellen') : p.requested === 'auto' ? t('Automatisch') : t('Textstellen')),
      h('span', null, `${v.max_chunks_per_doc >= 50 ? t('{k} Textstellen, ohne Grenze je Text', { k: v.top_k }) : t('{k} Textstellen, höchstens {m} je Text', { k: v.top_k, m: v.max_chunks_per_doc })} · ${t('{n} im Suchbereich', { n: texts })}`),
      p.auto && Object.keys(p.auto).length ? h('span', { class: 'plan-auto', title: autoTitle(p) }, t('angepasst')) : null];
  const hint = p.mode === 'passages' && p.band === 'very_many' && s.mode === 'answer'
    ? h('span', { class: 'muted' }, t('Viele Texte: Für Detailfragen den Suchbereich eingrenzen (Korpus, Sammlung in Zotero).'))
    : p.mode === 'passages' && p.reason === 'too_large' && p.stages_ok && p.n_docs <= 3 && s.mode === 'answer'
      ? h('button', { type: 'button', class: 'link-btn', title: t('Für Texte über der Grenze: kapitelweise lesen, je Etappe Notizen, dann die Antwort aus den Notizen. Langsamer und mit mehr Aufrufen, aber vollständig.'),
        onclick: () => { s.scopeMode = 'stages'; update(); } }, t('Vollständiger: in Etappen lesen (etwa {n})', { n: fmtInt(p.stages) })) : null;
  const why = p.mode === 'passages' && p.requested !== 'passages' && p.reason && p.reason !== 'search_only' && p.reason !== 'empty'
    ? h('span', { class: 'muted', title: reasonText(p) }, t('keine ganzen Texte: {r}', { r: reasonText(p) })) : null;
  const session = Object.entries(s.overrides);
  const label = val => typeof val === 'boolean' ? (val ? t('an') : t('aus')) : String(val);
  return h('div', { class: 'plan-line' }, h('div', { class: 'plan-line-inner' }, main, why, hint,
    p.mode === 'passages' && s.mode === 'answer' ? qtypeSelect(s) : null,
    session.length ? h('span', { class: 'plan-session' },
      session.map(([k, val]) => `${SESSION_LABELS()[k] || k} ${label(val)}`).join(' · '), ` ${t('(diese Sitzung)')} `,
      h('button', { type: 'button', class: 'link-btn', onclick: () => { s.overrides = {}; update(); } }, t('Zurücksetzen'))) : null));
}

function toolbar() {
  const s = st();
  const corpora = projectCorpora();
  const status = state.status;
  const values = s.corpora.filter(id => corpora.some(c => c.id === id));
  return h('div', { class: 'ask-toolbar' },
    s.scope ? h('div', { class: 'ask-scope' }, icon('zotero', 16),
      h('span', { class: 'chip chip-accent', title: t('Aus Zotero gewählt') }, h('span', null, s.scope.label),
        h('button', { class: 'chip-x', title: t('Wieder die Korpora des Projekts'), onclick: () => { s.scope = null; update(); } }, icon('x', 11))))
      : h('div', { class: 'ask-scope' }, icon('corpus', 16),
        corpora.length ? multiSelect({ values, label: t('Wissenskorpora'), class: 'ask-corpora',
          placeholder: t('Alle Korpora des Projekts ({n})', { n: corpora.length }),
          options: corpora.map(c => ({ value: c.id, label: c.name, hint: nTexts(nIndexed(c.stats)) })),
          onChange: v => { s.corpora = v.length === corpora.length ? [] : v; update(); } })
          : h('span', { class: 'muted small' }, t('Projekt ohne Korpus'))),
    segmented({ size: 'sm', value: s.mode, onChange: v => { s.mode = v; update(); }, options: [
      { value: 'answer', label: t('Antwort'), icon: 'ai' }, { value: 'search', label: t('Nur Textstellen'), icon: 'quote' }] }),
    s.mode === 'answer' ? scopeSwitch() : null,
    presetSwitch(),
    btn('', { icon: 'sliders', kind: s.showOptions ? 'secondary' : 'ghost', size: 'sm', title: t('Suchoptionen'), onClick: () => { s.showOptions = !s.showOptions; update(); } }),
    h('div', { class: 'spacer' }),
    status && h('button', { class: ['model-chip', !status.llm.key && 'warn'], title: t('Sprachmodell ändern'), onclick: () => { ui('settings', {}).tab = 'models'; navigate('settings'); } },
      icon(status.llm.key ? 'ai' : 'alert', 14), `${providerLabel(status.llm.provider)} · ${status.llm.model}`));
}

function optionsRow() {
  const s = st();
  if (!s.showOptions || !state.settings) return null;
  const r = state.settings.retrieval;
  const val = k => (s.overrides[k] ?? r[k]);
  const set = (k, v) => { if (v === r[k]) delete s.overrides[k]; else s.overrides[k] = v; update(); };
  return h('div', { class: 'ask-options' },
    field(t('Textstellen'), numberInput({ value: val('top_k'), min: 1, max: 100, int: true, onChange: v => set('top_k', v) }), { help: t('Wie viele Textstellen an das Sprachmodell gehen.') }),
    field(t('Max. pro Text'), numberInput({ value: val('max_chunks_per_doc'), min: 1, max: 50, int: true, onChange: v => set('max_chunks_per_doc', v) }), { help: t('Verhindert, dass ein einzelnes Werk alles dominiert.') }),
    field(t('Recherche-Runden'), numberInput({ value: val('research_rounds'), min: 0, max: 5, int: true, onChange: v => set('research_rounds', v) }), { help: t('Das Modell prüft die Treffer auf Lücken und sucht gezielt nach.') }),
    field(t('Mehrfachsuche'), toggle({ checked: val('multi_query'), label: val('multi_query') ? t('an') : t('aus'), onChange: v => set('multi_query', v) }), { help: t('Mehrere Suchanfragen auf Deutsch und Englisch mit Synonymen.') }),
    h('p', { class: 'muted small' }, t('Gilt nur für diese Sitzung und geht dem Preset vor – bis du unter Einstellungen → Experten etwas änderst oder ein anderes Preset wählst. Dauerhafte Werte und eigene Presets: Einstellungen → Experten.')));
}

function composer() {
  const s = st();
  return h('div', { class: 'composer' },
    h('div', { class: 'composer-box' },
      h('textarea', { id: 'ask-input', class: 'composer-input', rows: 2, value: s.draft, placeholder: t('Frage stellen … (Enter sendet, Umschalt+Enter für neue Zeile)'),
        oninput: e => { s.draft = e.target.value; },
        onkeydown: e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); s.draft = e.target.value; send(); } } }),
      s.busy ? btn(t('Stopp'), { icon: 'stop', onClick: stop }) : btn(t('Senden'), { kind: 'primary', icon: 'send', onClick: () => { const el = document.getElementById('ask-input'); s.draft = el ? el.value : s.draft; send(); } })));
}

function conversation() {
  const s = st();
  ensurePlan(s);
  const corpora = projectCorpora();
  const indexed = corpora.reduce((a, c) => a + (c.stats.embedded || 0), 0);
  // Deckt das aktive Embedding-Modell die Korpora nur teilweise ab (z. B. nach einem Modellwechsel)?
  const coverage = coverageNotice(corpora);
  const body = s.messages.length ? s.messages.map((m, i) => m.role === 'user'
    ? h('div', { class: 'msg user', key: 'm' + i }, h('div', { class: 'bubble' }, m.content))
    : assistantMessage(m, i))
    : h('div', { class: 'ask-empty' },
      emptyState('chat', t('Was möchtest du in deiner Bibliothek nachschlagen?'),
        t('Die Antwort stützt sich nur auf die indexierten Texte der gewählten Korpora dieses Projekts. Jeder Beleg führt zur Textstelle und öffnet auf Wunsch die PDF an der richtigen Seite.')),
      !corpora.length ? notice('info', t('Diesem Projekt ist noch kein Wissenskorpus zugeordnet. Lege fest, welche Sammlungen durchsucht werden sollen.'),
        h('div', { class: 'notice-actions' }, btn(t('Korpus anlegen oder hinzufügen'), { size: 'sm', kind: 'primary', icon: 'plus', onClick: () => navigate('corpora') })))
        : coverage && !indexed ? null
          : !indexed ? notice('warning', t('Die Korpora dieses Projekts sind noch nicht indexiert.'), h('div', { class: 'notice-actions' }, btn(t('Zur Indexierung'), { size: 'sm', onClick: () => navigate('index') })),
            { dismiss: `notindexed:${state.projectId}`, session: true })
            : h('div', { class: 'examples' }, examples().map(q => h('button', { key: q, class: 'example', onclick: () => { s.draft = q; update(); requestAnimationFrame(() => { const el = document.getElementById('ask-input'); if (el) el.focus(); }); } }, q))));
  return h('div', { class: 'conversation' },
    toolbar(), optionsRow(),
    coverage && h('div', { class: 'coverage-banner' }, coverage),
    h('div', { class: 'messages', onscroll: e => { const el = e.currentTarget; s.stick = el.scrollHeight - el.scrollTop - el.clientHeight < 60; },
      ref: el => { if (s.stick) el.scrollTop = el.scrollHeight; } }, h('div', { class: 'messages-inner' }, body)),
    planLine(),
    composer());
}

export function askView() {
  const s = st();
  syncOverrides(s);
  if (s.openChat) { const id = s.openChat; s.openChat = null; openChat(id); }
  const p = project();
  return h('div', { class: 'view-wrap' },
    viewHeader(t('Fragen'), p ? t('Projekt „{name}“ – Antworten aus deinen Texten, mit Belegen bis auf die Seite', { name: p.name }) : t('Antworten aus deinen Texten – mit Belegen bis auf die Seite')),
    h('div', { class: 'ask' }, chatList(), conversation()));
}

export { toast };
