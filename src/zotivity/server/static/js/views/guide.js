// Anleitung in zwei Teilen: zuerst die Einrichtung (das Technische, einmal je Computer – dieselbe Karte wie unter
// Einstellungen → Einrichtung), dann der Arbeitsablauf in vier Schritten (für jedes Vorhaben), je mit Links in die
// Ansichten und dem Stand im aktiven Projekt. Der erste Start kommt hierher, später die zuletzt gesehene Ansicht. Ist
// alles Nötige eingerichtet, ist die Einrichtung eingeklappt; fehlt etwas, klappt sie von selbst auf.
import { h } from '../core/vdom.js';
import { state, update, ui, project, projectCorpora } from '../core/store.js';
import * as api from '../core/api.js';
import { t } from '../core/i18n.js';
import { fmtInt, nIndexed, plural } from '../core/util.js';
import { icon } from '../ui/icons.js';
import { btn, badge, spinner } from '../ui/components.js';
import { navigate, viewHeader } from './shell.js';
import { newProjectDialog } from './projects.js';
import { setupCard, zoteroHelp } from './setup.js';

const link =(label, go, kind = 'ghost') => btn(label, { size: 'sm', kind, onClick: go });

// Nötige Schritte der Einrichtung, die fehlen. Ein geschlossenes Zotero zählt nicht dazu: Das meldet die Statusleiste,
// und die Einrichtung soll nicht jedes Mal aufklappen, wenn Zotero zu ist (gleiche Regel wie der Punkt in der Leiste).
export function setupMissing() {
  const s = state.setup;
  return s ? s.steps.filter(x => x.group === 'technical' && !x.optional && !x.done && x.id !== 'zotero') : [];
}

// Recherchen des aktiven Projekts mit ihrem Stand (neu geholt beim Öffnen der Anleitung, siehe navigate)
function researchOf(p) {
  const g = ui('guide', { for: null, research: null });
  const key = p ? p.id : null;
  if (p && g.for !== key) {
    g.for = key;
    api.get('/api/research/projects').then(r => { g.research = r.projects.filter(x => (x.settings || {}).zotllm_project === p.id); })
      .catch(() => { g.research = []; }).finally(update);
  }
  return p ? g.research : [];
}

function step(n, o) {
  return h('li', { key: n, class: ['setup-step', 'guide-step', o.done && 'done', o.current && 'current', o.optional && 'optional'] },
    h('div', { class: 'setup-mark' }, o.done ? icon('check', 15) : String(n)),
    h('div', { class: 'setup-text' },
      h('div', { class: 'setup-title' }, h('button', { type: 'button', class: 'guide-title', onclick: o.go }, o.title), o.optional ? badge(t('optional'), 'neutral') : null),
      h('div', { class: 'guide-what' }, o.text),
      o.status ? h('div', { class: 'setup-detail' }, o.status) : null,
      o.hint ? h('div', { class: 'setup-detail warn-text' }, o.hint) : null),
    h('div', { class: 'setup-action guide-links' }, o.links));
}

// Ein Teil der Anleitung: grosse Nummer, Titel, wofür er gilt, rechts ggf. ein Knopf
function section(n, title, sub, body, action) {
  return h('section', { class: 'guide-section' },
    h('div', { class: 'guide-section-head' },
      h('span', { class: 'guide-section-num' }, String(n)),
      h('div', { class: 'grow' }, h('h2', null, title), h('div', { class: 'muted small' }, sub)),
      action),
    body);
}

// 1. Einrichtung: aufgeklappt, solange Nötiges fehlt; sonst eine Zeile, die sich aufklappen lässt
function setupSection() {
  const s = state.setup;
  const g = ui('guide', {});
  if (!s) return section(1, t('Einrichtung'), t('einmal je Computer'), h('div', { class: 'pad' }, spinner(16)));
  const missing = setupMissing();
  const zoteroOff = s.steps.some(x => x.id === 'zotero' && !x.done);
  const optionalOpen = s.steps.filter(x => x.group === 'technical' && x.optional && !x.done).length;
  const open = missing.length > 0 || !!g.setupOpen;
  const toggle = missing.length ? null
    : btn(open ? t('Einklappen') : t('Details'), { size: 'sm', kind: 'ghost', icon: open ? 'chevronDown' : 'chevronRight', onClick: () => { g.setupOpen = !open; update(); } });
  const summary = h('div', { class: 'guide-setup-done' },
    icon(zoteroOff ? 'alert' : 'check', 16),
    h('div', { class: 'grow' },
      h('div', null, t('Alles Nötige ist eingerichtet.'),
        optionalOpen ? h('span', { class: 'muted' }, ' ', plural(optionalOpen, t('optionaler Schritt offen'), t('optionale Schritte offen'))) : null),
      zoteroOff ? h('div', { class: 'warn-text small' }, t('Zotero ist gerade nicht verbunden – Zotero öffnen; beim ersten Mal die lokale Schnittstelle erlauben.')) : null),
    zoteroOff ? link(t('Wie?'), zoteroHelp) : null);
  return section(1, t('Einrichtung'), t('einmal je Computer'), open ? setupCard({ embedded: true }) : summary, toggle);
}

// 2. Arbeitsablauf: vier Schritte mit dem Stand im aktiven Projekt
function workflowSection() {
  const p = project();
  const research = researchOf(p) || [];
  const corpora = p ? projectCorpora() : [];
  const docs = corpora.reduce((a, c) => a + c.stats.documents, 0);
  const indexed = corpora.reduce((a, c) => a + nIndexed(c.stats), 0);
  const chosen = research.reduce((a, r) => a + ((r.summary || {}).included || 0), 0);
  const worked = p ? (p.chats || 0) + (p.reviews || 0) : 0;
  const status = state.status;
  const done = { project: !!p, corpus: corpora.length > 0 && docs > 0 && indexed >= docs, work: worked > 0 };
  const current = !done.project ? 1 : !done.corpus ? 3 : !done.work ? 4 : 0;
  return section(2, t('Arbeitsablauf'), t('für jedes Vorhaben'), h('div', { class: 'stack' },
    h('ol', { class: 'setup-steps' },
      step(1, { title: t('Projekt anlegen'), done: done.project, current: current === 1, go: () => navigate('start'),
        text: t('Ein Projekt bündelt Recherchen, Korpora, Fragen und Forschungsstände zu einem Vorhaben.'),
        status: p ? t('Aktiv: {name}', { name: p.name }) : t('Noch kein Projekt.'),
        links: [link(t('Neues Projekt'), newProjectDialog, p ? 'ghost' : 'primary'), link(t('Zur Projektseite'), () => navigate('start'))] }),
      step(2, { title: t('Recherche'), optional: true, done: chosen > 0, go: () => navigate('research'),
        text: t('Neue Literatur in Fachdatenbanken suchen, sichten und nach Zotero übernehmen. Schon eine Sammlung in Zotero? Weiter bei Schritt 3.'),
        status: !p || research.length === 0 ? t('Noch keine Recherche in diesem Projekt.')
          : `${plural(research.length, t('Recherche'), t('Recherchen'))} · ${t('ausgewählt {n}', { n: fmtInt(chosen) })}`,
        links: [link(t('Zur Recherche'), () => navigate('research'))] }),
      step(3, { title: t('Korpus anlegen und indexieren'), done: done.corpus, current: current === 3, go: () => navigate('corpora'),
        text: t('Die Sammlungen aus Zotero wählen, mit denen du arbeiten willst. Zotivity liest die Texte ein; das läuft im Hintergrund.'),
        status: corpora.length ? `${plural(corpora.length, t('Korpus'), t('Korpora'))} · ${t('{a} von {b} Texten indexiert', { a: fmtInt(indexed), b: fmtInt(docs) })}` : t('Noch kein Korpus in diesem Projekt.'),
        hint: status && !status.zotero.local_api && !done.corpus ? t('Dafür muss Zotero laufen.') : null,
        links: [link(t('Zu den Korpora'), () => navigate('corpora')), link(t('Zum Index'), () => navigate('index'))] }),
      step(4, { title: t('Mit den Korpora arbeiten'), done: done.work, current: current === 4, go: () => navigate('ask'),
        text: t('Fragen an die eigenen Texte stellen oder einen belegten Forschungsstand schreiben.'),
        status: p ? `${plural(p.chats || 0, t('Chat'), t('Chats'))} · ${plural(p.reviews || 0, t('Forschungsstand'), t('Forschungsstände'))}` : null,
        hint: status && !status.llm.key ? t('Dafür fehlt noch ein Sprachmodell.') : null,
        links: [link(t('Fragen'), () => navigate('ask')), link(t('Forschungsstand'), () => navigate('review'))] })),
    h('div', { class: 'guide-more' },
      h('div', { class: 'row wrap' }, icon('search', 15), h('span', { class: 'grow' }, t('Jederzeit: Suchen durchsucht die eigene Bibliothek und alle Gruppen, auch ohne Sprachmodell.')),
        link(t('Suchen'), () => navigate('search'))),
      h('div', { class: 'row wrap' }, icon('settings', 15), h('span', { class: 'grow' }, t('Sprache, Modelle, Schlüssel und alles Weitere stehen unter Einstellungen.')),
        link(t('Einstellungen'), () => navigate('settings'))))));
}

export function guideView() {
  if (state.setup && state.setup.first_run) {
    state.setup.first_run = false;
    api.post('/api/setup/done').catch(() => {});
  }
  return h('div', { class: 'view-wrap' },
    viewHeader(t('Anleitung'), t('Einrichtung und Arbeitsablauf')),
    h('div', { class: 'view-body scroll' }, h('div', { class: 'guide-inner stack-lg' },
      h('p', { class: 'guide-intro' }, t('Zuerst richtest du Zotivity einmal auf diesem Computer ein. Danach führt dich der Arbeitsablauf für jedes Vorhaben vom Projekt bis zum fertigen Text.')),
      setupSection(),
      workflowSection())));
}
