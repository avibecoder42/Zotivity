// Führung durch die Recherche: der Stand je Reiter (Häkchen, Zahlen) und die Kopfzeile darunter – was ist hier zu tun, wie
// geht es weiter? Jeder Reiter hat immer ein „Weiter“; es sperrt nie, die Reiter bleiben frei wählbar. Blau wird der Knopf
// erst, wenn im Reiter selbst kein Schritt mehr offen ist (solange ist dort ein anderer Knopf blau).
import { h } from '../../core/vdom.js';
import { t } from '../../core/i18n.js';
import { fmtInt } from '../../core/util.js';
import { btn } from '../../ui/components.js';
import { rs, TABS, openTab } from './common.js';
import { draft, suggestionOpen } from './project.js';
import { strategyState } from './strategy.js';
import { navigate } from '../shell.js';

const summary = () => (rs().project && rs().project.summary) || {};
const sources = () => (rs().project.settings || {}).sources || [];
// Neue Recherchen beginnen mit PubMed und OpenAlex: Erledigt ist der Reiter erst, wenn jemand gewählt oder schon damit gearbeitet hat
const sourcesChosen = () => {
  const x = sources();
  return ((rs().project.settings || {}).disciplines || []).length > 0 || summary().queries > 0
    || (x.length > 0 && !(x.length === 2 && x.includes('pubmed') && x.includes('openalex')));
};
const openCount = sum => (sum.visible || 0) - (sum.screened || 0);
// Aus dem Entwurf, damit sich der Stand schon beim Tippen ändert
const hasQuestion = () => { const d = draft().d; return !!`${d.question || ''}${d.description || ''}`.trim(); };
const folders = () => draft().d.folder_map.filter(f => (f.path || '').trim());

// Stand eines Reiters: done (Häkchen) und count (Zahl hinter dem Namen)
export function tabState(id) {
  if (!rs().project) return {};
  const sum = summary();
  switch (id) {
    case 'project': return { done: hasQuestion() };
    case 'databases': return { done: sourcesChosen() };
    case 'strategy': return { done: sum.queries > 0 };
    case 'search': return { done: sum.pool > 0 && !sum.queries_open };
    case 'pool': return { count: sum.pool ? fmtInt(sum.pool) : '' };
    case 'folders': return { done: folders().length > 0 };
    case 'screening': return { done: sum.visible > 0 && !openCount(sum), count: sum.visible ? `${fmtInt(sum.screened)}/${fmtInt(sum.visible)}` : '' };
    case 'handover': return { done: sum.included > 0 && !sum.handover_open };
    case 'fulltext': return { done: sum.included > 0 && sum.fulltext_done >= sum.included };
    default: return {};
  }
}

const tabLabel = id => TABS.find(x => x.id === id).label();
const next = (id, ready) => ({ label: t('Weiter: {x}', { x: tabLabel(id) }), go: () => openTab(id), ready });
const toCorpus = ready => ({ label: t('Weiter: Korpus anlegen'), go: () => navigate('corpora'), ready });
const suggestionText = () => t('Der KI-Vorschlag ist noch offen: Übernimm, was passt, oder verwirf ihn.');

// Was ist im aktuellen Reiter zu tun? { text, actions }
function guidance() {
  const sum = summary();
  switch (rs().tab) {
    case 'project':
      return hasQuestion() ? { text: t('Die Fragestellung steht. Als Nächstes wählst du die Datenbanken.'), actions: [next('databases', true)] }
        : { text: t('Trag eine Fragestellung oder Kurzbeschreibung ein. Darauf bauen die KI-Vorschläge auf.'), actions: [next('databases', false)] };
    case 'databases': {
      const n = sources().length;
      if (suggestionOpen()) return { text: suggestionText(), actions: [next('strategy', false)] };
      if (n && !sourcesChosen()) return { text: t('PubMed und OpenAlex sind vorgewählt. Wähle deine Disziplinen, dann schlägt die App passende Datenbanken vor.'), actions: [next('strategy', false)] };
      return n ? { text: t('Datenbanken gewählt: {n}. Als Nächstes baust du die Suchstrategie auf.', { n: fmtInt(n) }), actions: [next('strategy', true)] }
        : { text: t('Wähle mindestens eine Datenbank.'), actions: [next('strategy', false)] };
    }
    case 'strategy': {
      const x = strategyState();
      if (x.ai) return { text: suggestionText(), actions: [next('search', false)] };
      if (!x.blocks && !sum.queries) return { text: t('Lege Konzeptblöcke an oder lass sie dir vorschlagen. Fertige Suchstrings gehen auch direkt unter Suche & Import.'), actions: [next('search', false)] };
      if (x.open) return { text: sum.queries ? t('Die Suchstrings haben sich geändert oder sind noch nicht alle übernommen.') : t('Übernimm die Suchstrings, die du in der App suchen willst (rechts).'), actions: [next('search', false)] };
      if (x.built && !x.live && !sum.queries) return { text: t('Diese Datenbanken laufen über den RIS-Weg: Suchstrings kopieren, dort suchen und die RIS-Dateien unter Suche & Import laden.'), actions: [next('search', true)] };
      return sum.queries ? { text: t('Die Suchstrings sind übernommen. Als Nächstes suchst du in den Datenbanken.'), actions: [next('search', true)] }
        : { text: t('Ergänze die Begriffe in den Blöcken, dann entstehen rechts die Suchstrings.'), actions: [next('search', false)] };
    }
    case 'search':
      if (sum.queries_open) return { text: t('Suchstrings noch nicht gesucht: {n}.', { n: fmtInt(sum.queries_open) }), actions: [next('pool', false)] };
      if (sum.pool) return { text: t('{n} Treffer im Pool. Als Nächstes prüfst du den Pool.', { n: fmtInt(sum.pool) }), actions: [next('pool', true)] };
      return sum.queries ? { text: t('Noch keine Treffer. Prüfe die Suchstrings oder lade RIS-Dateien.'), actions: [next('pool', false)] }
        : { text: t('Füge einen Suchstring hinzu oder lade RIS-Dateien.'), actions: [next('pool', false)] };
    case 'pool':
      if (!sum.pool) return { text: t('Der Pool ist noch leer. Zuerst unter Suche & Import suchen.'), actions: [next('folders', false)] };
      return sum.in_zotero_other ? { text: t('Das Ziel hat gewechselt: Bitte neu abgleichen.'), actions: [next('folders', false)] }
        : { text: t('{n} Treffer ohne Duplikate. Als Nächstes legst du das Ziel und die Ordner fest.', { n: fmtInt(sum.pool) }), actions: [next('folders', true)] };
    case 'folders': {
      const n = folders().length;
      return n ? { text: t('Ordner bereit: {n}. Als Nächstes sichtest du die Treffer.', { n: fmtInt(n) }), actions: [next('screening', true)] }
        : { text: t('Lege mindestens einen Ordner an: Das Screening nimmt Treffer in Ordner auf.'), actions: [next('screening', false)] };
    }
    case 'screening':
      if (!sum.visible) return { text: t('Noch nichts zu sichten.'), actions: [next('handover', false)] };
      if (openCount(sum)) return { text: t('Noch {n} Treffer offen.', { n: fmtInt(openCount(sum)) }), actions: [next('handover', false)] };
      return sum.included ? { text: t('Alles gesichtet. Als Nächstes übergibst du die Auswahl an Zotero.'), actions: [next('handover', true)] }
        : { text: t('Alles gesichtet, aber noch nichts aufgenommen.'), actions: [next('handover', false)] };
    case 'handover':
      if (!sum.included) return { text: t('Noch nichts aufgenommen. Was du im Screening aufnimmst, erscheint hier.'), actions: [next('fulltext', false)] };
      if (sum.handover_open) return { text: t('{n} aufgenommene Treffer warten auf die Übergabe an Zotero.', { n: fmtInt(sum.handover_open) }), actions: [next('fulltext', false)] };
      return { text: t('Alles übergeben. Optional prüfst du die Volltexte, sonst legst du einen Korpus an.'), actions: [next('fulltext', false), toCorpus(true)] };
    case 'fulltext':
      if (!sum.included) return { text: t('Noch nichts aufgenommen.'), actions: [toCorpus(false)] };
      return sum.fulltext_done >= sum.included ? { text: t('Alle Volltexte sind geprüft. Als Nächstes legst du einen Korpus an.'), actions: [toCorpus(true)] }
        : { text: t('Optional: Stand der Volltexte festhalten. Als Nächstes legst du einen Korpus an.'), actions: [toCorpus(false)] };
    default:
      return { text: '' };
  }
}

export function nextBar() {
  const { text, actions = [] } = guidance();
  return h('div', { class: 'rs-nextbar' },
    h('div', { class: 'rs-nexttext' }, text),
    actions.map(a => btn(a.label, { key: a.label, size: 'sm', kind: a.ready ? 'primary' : 'secondary', icon: 'chevronRight', onClick: a.go })));
}
