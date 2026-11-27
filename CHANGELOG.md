# Changelog

All notable changes, newest first.

## 1.3.0 – 2026-11-27

### Changed
- **New PDF engine: PDFium (pypdfium2) instead of PyMuPDF.** PyMuPDF is licensed under AGPL or a commercial
  licence; PDFium is BSD/Apache. Text, paragraphs, running heads and printed page numbers come from the same steps as
  before. Compared on 18 real PDFs (articles and books up to 659 pages): 99.6 % of the characters, 98.9 % identical
  words, page numbers identical on every page, about 5 ms per page. Text drawn twice for bold print is no longer
  read twice. Maths letters in formulas (italic N, x) become ordinary letters, so a search for "N = 120" finds
  them; emojis stay. EPUB files are read chapter by chapter from the archive.
- **Text recognition (OCR) runs `tesseract.exe`** (searched on the path and under Program Files\Tesseract-OCR) on a
  rendered image of the page; the setting and its result are the same as before.

### Added
- **Figures in PDFs** (Settings → Index → Figures, off by default). Images and drawn charts are found while reading,
  with caption and the text inside the chart. "Describe" has the helper or main model describe them – with the image
  if the model understands images – after showing the estimated tokens. The descriptions become searchable with their
  page, appear as "Figure (machine-described)" and are not quoted verbatim. Reading the same file again keeps them.
  **Describe automatically after each indexing run** (off by default) starts a separate task for the figures still
  open, at most 200 per run (adjustable), figures with a caption first; it pauses for six hours if a run produced no
  description. **Scope** shows the numbers and describes for a single corpus instead of the whole index. Whole texts
  under Ask carry their described figures as a section with page.
- **Questions about a figure:** "Describe Figure 1 in …", "Fig. 3", "Abb. 2 and 4" find the figure by the number in
  its caption – in the named text, via the first author, the previous question, or (if unambiguous) the whole scope.
  The figure and the passage with its caption come first. If it is not described yet, the answer relies on caption
  and text and says so; **Describe the figure and answer again** describes just this figure and asks again.
- **Named text first:** if a question contains the title of a text in scope, its passages are searched separately
  and fill a little over half of the budget; the basis line names it.
- **Texts with errors:** a run that leaves texts with errors or without text in its scope says so ("All other texts
  are up to date. Not read again – with errors or without text: 16.") and offers **Try again**; per corpus also via
  ↻ in the corpus list under Index and "Try texts with errors again" in the corpus menu. After an update, the first
  run reads texts with errors again once by itself.

### Fixed
- **Closing Zotivity no longer hangs** on running background tasks (e.g. reading in stages): they are cancelled, and
  after a few seconds Zotivity ends regardless. The setup ends a Zotivity that does not respond instead of showing
  "Zotivity is still running" again and again.
- OpenAI-compatible servers: a parameter the server rejects (e.g. a reasoning effort it does not know) is left out
  and the request repeated – also for requests running at the same time, which so far failed with an error.

## 1.2.9 – 2026-11-26

### Added
- **Backup to a folder of your choice** (Settings → Index). Zotivity copies the index with projects, chats and
  literature reviews, the database searches (`research.db`), the settings and your own citation styles to a folder
  you choose, e.g. in switchdrive, OneDrive or on a USB stick: daily or weekly (after indexing runs and shortly after
  start-up) or only by hand. The newest three backups are kept (adjustable); each is a subfolder of its own with
  restore instructions, and other files in the folder are left alone. API keys stay in the credential store.

### Changed
- **Overview questions under Ask also get passages from the full texts.** Next to one card per text (abstract,
  details, evaluation sheet), the model now receives the best matching passages, so an overview can name details and
  quote with page numbers. Before, an overview answer saw only abstracts and evaluation sheets.
- **Comparisons of concepts, definitions or positions count as detail questions**, so they are answered from
  passages of the full texts. "Overview" is kept for questions about the texts as a whole (which texts, methods,
  samples or results there are).

## 1.2.8 – 2026-11-23

### Added
- **"Test" button for database keys.** Under Settings → Databases each stored key (OpenAlex, PubMed/NCBI, Semantic
  Scholar, Zotero web API) can be tested with one small request. The result says whether the key works, is rejected,
  the service is throttling (HTTP 429, not the key's fault) or cannot be reached. The key itself is never shown.
- **Privacy policy** ([English](https://github.com/avibecoder42/Zotivity/blob/main/PRIVACY.md), [Deutsch](https://github.com/avibecoder42/Zotivity/blob/main/PRIVACY.de.md)), also shown by the installer before
  installation. It lists every connection; the update check is the only one Zotivity makes on its own.
- **Installer checkbox "Check for updates at start-up".** Clearing it turns the update check off from the first
  start (setting `check_updates_on_start`). Updates started from within Zotivity keep the setting.
- The program file and the installer name the copyright holder and the licence in their file properties.

### Fixed
- **Semantic Scholar: "too many requests" although a key is stored.** Counting hits now fetches only the paper IDs
  instead of all fields with abstracts (up to about 1.3 MB per count), which made the service throttle. With a key,
  Zotivity waits longer before trying again (10, 20, 40 seconds), and the message no longer asks for a key that is
  already there.
- **Outdated evaluation sheets.** A sheet made before its text was read again (e.g. a scan after text recognition) was
  reused by new literature reviews. A sheet now remembers the version of the text it was made from. A new literature
  review recreates an outdated sheet; existing ones mark it as "outdated" and name the sections that cite the text.
- **A file replaced outside Zotero (e.g. after OCR) is no longer read twice.** Zotivity stores the checksum of the file
  it actually read instead of the one Zotero reports, which Zotero updates only later.

## 1.2.7 – 2026-11-16

### Added
- **Whole texts under Ask.** If the texts in scope fit the language model together (a limit per provider under
  Settings → Expert, cautious defaults, small for Ollama), the model gets the whole texts with page marks instead of
  selected passages. A question about a book or a few articles is answered from the whole text. Citations lead to the
  PDF page. Follow-up questions in the same chat use prompt caching. Triggered automatically from Zotero, by the new
  switch **Scope: Automatic · Passages · Whole texts**, or by **Ask this text** in the list of texts in a corpus.
- **Plan line and basis.** Before sending, a line shows what will happen (whole texts or passages, amount of text,
  active session values with a reset). Under each answer, "Basis: 18 passages from 7 of 100 texts" or "whole text".
- **"Automatic" adapts to the number of texts.** Passages, the limit per text, diversity and neighbouring sections
  follow the number of texts in scope (1–3, 4–30, 31–200, more). Values set deliberately under Expert stay; it can be
  switched off for fixed values.
- **Question type and overview cards.** The helper model classifies a question as detail, overview or evidence; the
  plan line shows it and it can be set by hand. Overview and comparison questions get a short card per text (abstract,
  details, evaluation sheet if there is one), so every text in scope appears.
- **Reading in stages.** Texts above the limit (up to 5) can be read chapter by chapter with the new scope **In
  stages**: notes per stage with page numbers (helper or main model, Settings → Expert), then the answer from all
  notes. Slower, but complete; the notes are shown below the answer.

### Changed
- **Search options under Ask no longer override later changes.** A change under Settings → Expert or a preset switch
  drops the session values: what was set last wins. "Max. per text" goes up to 50.
- **Texts in a corpus (Index): open work first.** Open texts and problems are at the top of the list, indexed texts
  at the bottom.

### Fixed
- **Quote check in literature reviews.** Quotation marks are paired by direction («…», »…«, „…“, “…”, "…"). A short
  «word» no longer turns the running text up to the next guillemet into an "unclear quote", and parts of the review's
  own question are not checked as quotes.
- **"For all: copy from …" for duplicates** picks the copy in the corpus among equally good copies of that library,
  as the rule does, instead of the first one found.
- **Scans with a stamp line on every page** (e.g. "Kopie von subito e.V. …") are recognised as scans, so text
  recognition (OCR) can start. A Zotero full text that consists only of such stamps is not indexed as text.

## 1.2.6 – 2026-11-16

### Added
- **Updates from GitHub.** At start-up Zotivity asks GitHub for the latest release and offers to update (can be
  switched off under Settings → Program & data). **Check for updates** asks at any time; **Versions on GitHub** opens
  the releases page. Each release now carries the update information `zotivity-update.json`. The update source is
  fixed: the free update-source field is gone, so nobody can slip in a foreign installer through the settings. GitHub
  sees the computer's IP address, nothing else. This version still has to be installed by hand once.
- **Project export keeps the PDF pages of citations.** A literature review imported on another computer opens each
  citation on the right PDF page in Zotero even before the texts are indexed there – provided it is the same PDF file
  (same attachment and checksum). Clicking citations needs neither the index nor an embedding model.

### Fixed
- **The list of texts in a corpus updates after a sync with Zotero.** Items newly added in Zotero now appear under
  Open by themselves, also when the sync ran in the background. Before, they only appeared after "Reload" or a restart.
- **Citations without a page find their page again after indexing.** For a text that was not read yet (e.g. after a
  project import), Zotivity no longer remembers "no page" for good; it searches again once the text is indexed.

## 1.2.5 – 2026-11-15

### Changed
- **The Guide now has two parts: Setup and Workflow.** Setup (Zotero, language model, embedding model and the optional
  steps, once per computer) moved from Settings to the top of the Guide; the four steps of the workflow follow below. The
  first start opens the Guide, later starts the view used last, the Guide included. Once everything needed is set up,
  Setup is collapsed to one line (**Details** expands it); if something is missing later, it expands by itself and the
  Guide entry in the navigation bar shows a dot. A closed Zotero is shown as a note, not as missing setup. Setup also
  stays under Settings → Setup.

## 1.2.4 – 2026-11-12

Search strings for Ovid PsycInfo and Web of Science, checked in real searches, and literature in German, French and
Italian in the database search.

### Added
- **Database search: languages of the literature.** Under Research question you choose German, French, Italian and/or
  English. The AI then suggests search terms in these languages – each block also gets terms in the other languages,
  because such studies often have no English title or abstract – and databases with literature in these languages.
  Without a choice, English and the language of the research question are set. Before, the AI suggested English terms
  only.
- **SSOAR** (Social Science Open Access Repository) in the database catalogue, via RIS import. The database list shows
  which sources cover German, French or Italian literature (FIS Bildung, SSOAR, swisscovery).

### Fixed
- **Web of Science: terms of several words are searched as phrases.** Without quotation marks Web of Science links the
  words with AND, so "social medi*" found every record with "social" and "medi…" anywhere in the topic. Such terms are
  now quoted, also with an asterisk, which Web of Science accepts. In a real search this gave 84 instead of 3,172 hits.
- **Ovid PsycInfo: terms with "and" stay together.** A term such as "information and communication technolog*" was read
  by Ovid as an AND search and narrowed the whole block without any message. Terms with and, or, not or proximity
  operators are now quoted; truncation works inside the quotes.
- **Ovid PsycInfo: clearer notes.** The combining line needs the numbers of your own search history, and a new note
  explains Ovid's message "The subject heading … is invalid in this database" and where to find the valid term.
- **RIS import shows what was added.** After dropping a file, only a message flashed at the bottom. The RIS import card
  now keeps the result (records, new in the pool, already there) with **Go to pool**, and lists the files imported so
  far. Without search strings, the left side no longer looks as if nothing had happened.
- **Project page: the next step names the missing synchronisation.** For a corpus whose library is not synchronised
  yet, the line said the texts were searchable; it now asks to synchronise first.

## 1.2.3 – 2026-11-12

Importing a project on another computer.

### Fixed
- **Import starts the synchronisation with Zotero.** A project file holds no titles, authors or years, only which Zotero
  collections a corpus uses. Until Zotivity had synchronised those libraries on the new computer, literature reviews showed
  "n.a. n.d." instead of author and year and found no PDF. If Zotero is running, the import now starts this synchronisation
  right away; the import dialog says so.
- **Notice with "Synchronise now".** As long as a library of the project is not synchronised, the project page and the
  literature review say so and offer **Synchronise now**; the project tile shows "Not synchronised with Zotero". The review
  reloads by itself once the synchronisation is finished.
- **Texts of a library that is not synchronised no longer count as deleted.** Opening a literature review before the
  synchronisation stored the cited texts as deleted in Zotero and could switch their citations to a copy of the same work in
  another library. Now only libraries that are synchronised count, and only entries that Zotero reports as deleted are stored
  as such. The check no longer reports these texts as deleted either.

## 1.2.2 – 2026-11-12

Fixes from a review of the whole code with Claude Fable 5.2: the findings that could cost money or data.

### Fixed
- **Literature review: writing continues where it stopped.** After an error or a cancelled run, the next run wrote every
  section again. It now keeps what is written and continues with the first missing section. **Rewrite** for the whole text
  asks first.
- **Literature review: Save outline keeps the written text.** Saving the outline deleted all sections, even after changing
  only a word count. The text now stays with its section when you move, rename or change it; the app asks only if you removed
  a section that already has text.
- **Bibliography is complete and sorted.** A cited text that Zotero did not return was missing without notice; it now gets
  an entry from the stored data. Exports with more than 50 entries are sorted as one list, no longer in blocks of 50.
- **Database search: translating a PubMed string no longer changes the search silently.** Unknown field tags such as `[la]`
  and unbalanced parentheses give a note, a tag applies to the whole phrase before it, and mixed AND/OR chains keep the
  reading of PubMed.
- **Database search: search protocol and flow diagram show the same numbers.** The protocol counted superseded runs and left
  out decided records hidden by a prefilter.
- **Database search: the same title alone no longer counts as "already in Zotero".** Year, DOI and PMID must not contradict,
  as in the pool. Before, a preprint or a new edition could hide the record and be filed instead of it.
- **Screening: the card stays on the same record** when the list reloads during an AI run, and a list that arrives late no
  longer appears under another selection.
- **Views no longer repeat a failed request without pause.** Search, the tabs of the database search and Settings → Expert now
  wait before the next attempt and offer **Try again**.
- **Update: only the installer of the configured update source.** Address and checksum came from the request; they now come
  from the manifest of the update source, and nothing starts without a matching checksum.

## 1.2.1 – 2026-11-05

### Fixed
- **Duplicates: a copy with a file no longer loses to a copy without one.** If a PDF yielded no readable text, it counted the
  same as no PDF at all, and the preferred library could win with a copy that has no file. The work then seemed to be missing
  from the corpus. It now appears under problems, with the hint about text recognition.
- **Duplicates: the copy from the collections you chose for a corpus comes first.** Among equally good copies, the dialog before
  indexing preselects the copy from the corpus, even if another copy has already been read. Runs for one corpus or collection
  that do not ask choose the same way.

### New
- **Index → Duplicates: choose again for a corpus.** This opens the selection for all works of a corpus, with its copies
  preselected. Nothing changes until you apply it.

### Changed
- **Index → Last run:** a cancelled run now shows the current status of the corpus, with the same numbers as the corpora table,
  and the button is called Index like there, because it reads what is open now and does not continue the old list.

## 1.2.0 – 2026-11-04

### New
- **Mistral** as a language model provider. Mistral Large 4 and Small 4 are suggested; with a key, the app loads the current
  model list. For processing in the EU, change the server address to `https://api.eu.mistral.ai` (10 % surcharge, not all
  models).
- **Database search shows the way:** the tabs show the status (a tick when done, numbers for the pool and screening), and a
  line below them says what to do next. Every tab has **Next**, which turns blue once nothing is open in the current one; nothing
  is locked. After the handover, it leads on to creating a corpus.

### Changed
- **Search no longer belongs to a project.** It moved down to the views that apply to all projects (Ctrl+6 stays). There is one
  search history; existing histories are merged. Switching projects keeps the fields, results and selection. Items can be added
  to any corpus, and results show all corpora containing them. Search history is no longer part of `.zoty` files; older files
  still import it.
- **Database search, clearer names:** the tab "Project" is now "Research question", so that "project" only means the Zotivity
  project; "Folders" is now "Target & folders" and also holds the target in Zotero. The target library can be chosen right at the
  check in the pool.
- **AI suggestion for folders** now also uses a sample of the titles in the pool, not only those of included works.
- **AI buttons without a research question** now say visibly what is missing instead of only being greyed out.
- **Database search:** one rule for button colours. Blue is the next step, at most one per area; violet starts a language
  model; white is for other actions. In Search & import, for example, "Search all" is blue while something has not been
  searched yet, and it follows "Add search string".
- **Database search:** areas with AI suggestions are tinted violet. "Apply all" under search strings per source is now called
  "Apply all as search strings", because the AI suggestion has a button with the same name.

### Fixed
- **Zotivity no longer disturbs Zotero while it starts.** Zotero opens its interface before its items are ready. A request from
  Zotivity in that moment could apparently leave Zotero stuck on "Loading items" until it was restarted. Zotivity now only pings
  until Zotero is ready: the Zotero plugin shows that the start is finished; without the plugin, Zotivity waits 20 seconds.
- **A hanging Zotero is reported correctly and quickly.** Instead of waiting two minutes and then saying that Zotero is not running,
  Zotivity says after a few seconds that Zotero is running but its interface does not respond, and asks to restart Zotero.
- After a change of the target library, the pool named the new library for matches found in the old one. It now asks for a new
  check, and the new check removes the old matches. What was already handed over stays linked.

## 1.1.1 – 2026-11-02

### Fixed
- **Scrolling with two fingers no longer zooms.** During a two-finger scroll on a touchpad, Windows also reports small
  scaling movements. They added up across gestures until the interface zoomed, most noticeably in long lists such as the
  collections of a corpus. Now only a deliberate pinch within one gesture zooms.

### Changed
- **Buttons that start a language model are violet**, for example AI suggestion, Create evaluation sheets, Write sections
  and Shorten. Send in Ask stays blue, and so do buttons that only apply a suggestion.
- **Settings → Setup:** the button leading on to the Guide is always highlighted.

## 1.1.0 – 2026-11-01

**ZotLLM is now Zotivity.** The name fits what the application has become: comprehensive scholarly work with your Zotero
library, not only language models.

### Moving from ZotLLM
- On the first start, Zotivity takes over the data folders, settings and stored API keys of ZotLLM automatically.
- Projects are saved as **`.zoty`** files; `.zllm` files from earlier versions can still be imported.
- **Replace the Zotero plugin:** remove the old "ZotLLM" plugin in Zotero and install the new one (Settings → Zotero and
  export → Save plugin). The setup checklist reminds you. Word documents with existing citations keep working.
- The installer updates an existing ZotLLM installation and removes its old program folder. The command `zotllm` and the
  environment variables `ZOTLLM_*` still work.

### New
- **Database search** (new project view, Ctrl+2; German: “Recherche”): the step before the literature review. A search
  project leads from the research question to the selected literature in Zotero, in nine tabs (Alt+1 … Alt+9). Everything
  is saved automatically:
  - **Project** and **Databases:** research question, criteria, up to three reviewers, target library in Zotero;
    disciplines and a database catalog with access, terms of use and keys.
  - **Search strategy** and **Search & import:** concept blocks → search strings per database with live hit counts; AI
    suggestions appear directly with the blocks;
    searches in PubMed, OpenAlex, Europe PMC, ERIC, EconBiz, Semantic Scholar, arXiv, OSF Preprints and swisscovery;
    RIS import for licensed databases (PsycInfo, CINAHL …) with the date of the search; other sources for the log. No
    browser automation.
  - **Pool:** de-duplicated records, check against the Zotero target library, prefilters that only mark (with
    exception terms that turn records into borderline cases), missing abstracts fetched, recall check with key papers.
  - **Folders** 1–20 (shortcuts 1–9) mapped to Zotero collections; **Screening** as cards (J/K, 1–9, X, ?, G, A, Z)
    or as a list with bulk apply, recording who decides; **Handover** to Zotero only after explicit confirmation, search
    log in the style of PRISMA-S and a PRISMA 2020 flow diagram (SVG).
  - **Full texts** (optional): status per included text, detected from Zotero where possible – still missing, available,
    not retrievable, included, excluded with reason. Missing PDFs hold nothing up. Items excluded at full text can be
    taken out of the search's collections after confirmation and tagged; nothing is deleted.
  - **Report for the methods section** (optional): a language model drafts sources, search, criteria, selection process
    and selection flow from the log data, in German or English. Missing information appears as a placeholder, and
    numbers not found in the log data are reported. It does not replace a complete PRISMA report.
  - Optional **AI suggestions** (screening, databases, concept blocks, subfolders, report) with the model of the pre-selection
    in literature reviews; token and cost estimate before each run; decisions are always made by hand.
  - Every database search belongs to a Zotivity project; the project page lists them with their screening status, and
    searches without a project (e.g. after deleting one) can be assigned again. Data lives in `research.db` in the data folder.
  - Keys for the databases are stored under **Settings → Databases**, the key for the optional Zotero web API under
    **Settings → Zotero and export**. Searches write through Zotero on this computer by default.

### Changed
- **Project views in the order of the workflow:** Project, Database search, Corpora, Ask, Literature review, and set
  apart Search (Ctrl+1 … Ctrl+6). Index moves to **Ctrl+8**, the CSL editor stays on Ctrl+7.
- **Setup is only the technical part** and lives under Settings → Setup, where the first start leads. Zotivity then reopens
  the last project in the view used last, even if Zotero is closed.
- **New Guide**, the top entry of the navigation bar: create a project, database search (optional), create and index a
  corpus, work with the corpora – with links into the views and the status in the active project. The project page names
  the next step.
- **Layout:** reading text and forms stay readable and centred; lists and tables use the full width. Panels on the right
  adapt to the window until you drag them. The project page tiles no longer wrap as 4 + 1.
- **Zoom:** in the program window the whole interface can be zoomed (status bar, Ctrl + mouse wheel, pinch, Ctrl +/−/0);
  the value is kept across restarts.

### Also new
- Language models can answer with a **JSON schema** where the provider supports it (Claude structured outputs, Gemini,
  OpenAI); otherwise the schema is part of the request.

## 1.0.1 – 2026-10-29

### New
- "Check" shows the written words against the planned length and lists **sections that are too long** (more than
  20 % over their plan). **Shorten** has the language model tighten a section to its planned length without adding
  anything new – citations and verbatim quotations stay unchanged or are dropped; **Shorten all** does this for all of
  them. The outline shows the words actually written next to each section's word count.
- The target length is explained: it refers to the text with headings and citations; the bibliography and the
  "Literature search method" appendix come on top (Word's word count includes them).

### Fixed
- **Length of literature reviews:** language models wrote much more than planned (e.g. about 2,400 instead of 1,600
  words). Each section's word count from the outline is now a binding upper limit in the writing prompt; if a
  section runs long, the following ones get correspondingly less room (down to at most 60 % of their plan).

## 1.0.0 – 2026-10-29

### New
- **CSL editor** (new tab, Ctrl+7): find, adapt and install citation styles. This is a first version and still in
  development – feedback, bug reports and contributions are welcome as
  [issues](https://github.com/avibecoder42/ZotLLM/issues) or pull requests.
  - Search the roughly 10,000 styles on zotero.org (the list is only loaded when you search – about 300 KB once,
    afterwards only when it has changed), open styles installed in Zotero or import a .csl file.
  - Other styles are adopted as a copy with their own name and ID, so the original is never overwritten; dependent
    styles become an independent copy of their parent style.
  - Preview with citeproc-js, the software Zotero itself uses: in-text citations and bibliography entries for nine
    typical document types, plus your own Zotero items, in a choice of languages.
  - Click a part of a citation to edit exactly that part: text before and after, italic, bold, small caps,
    capitalisation, name format ("and" or "&", initials, order, "et al."), date form, page labels; move, remove or add
    fields. Parts used in several places are flagged, and the examples changed by the last edit are marked.
  - Code view with live preview and error messages, undo/redo, earlier versions.
  - "Install in Zotero" uses the ZotLLM plugin and Zotero's own install function: the style is immediately available in
    Zotero, in the Word plugin and in ZotLLM. Without the current plugin, the style is saved as a .csl file.
- The Zotero plugin can install styles from the CSL editor – reinstall it once (Settings → Zotero and export → Save
  plugin).

### Changed
- **Literature review in five steps:** "Keywords" (search queries) and "Select literature" are now separate steps.
  Every step has the same layout: the steps at the top, below them the collapsed prompt (title, research question,
  corpora) on the left and the buttons for the next step on the right, always in the same place.
- After writing, the review switches to "Check" and shows the text there; the check findings are collapsed into one
  summary line.
- **Evaluation sheets can be viewed:** in "Evaluate", a click on a text shows its sheet – fields, key findings, key
  quotations (opening the PDF in Zotero at the page) and keywords. "Recreate sheet" re-reads a single text.
- Outline sections can now also be moved up.
- **CSL editor:** a change can apply only to in-text citations or only to the bibliography, even where both use the
  same macro (e.g. the year) – the clicked part gets its own copy of the macro. The filter for styles installed in
  Zotero also searches short titles and ignores accents ("fhnw" finds "Schreibberatung PH FHNW").
- **CSL editor – examples:**
  - "Choose item types": besides the nine default examples, every other item type Zotero knows (e.g. case,
    statute, conference paper, film); default examples can be hidden. The list comes from Zotero's schema.
  - Examples come from your own library where a matching item exists (for articles with the matching number of
    authors); otherwise made-up English examples, mapped to CSL the way Zotero does it.
  - "Collection …": show the items of a Zotero collection as examples.
- **CSL editor – moving parts:** "Earlier"/"Later" leave a group at its edge, "To the start"/"To the end" jump
  there. If the selected part stands alone (like the year in a date macro), the place of the macro or the whole
  condition is moved – e.g. the year to the end of the bibliography entry. The bar above the selection shows the
  path through the macros.
- **CSL editor – "et al.":** clearer fields ("et al." from … names / of these, show) with an example.

### Fixed
- Static files in folders starting with "v" were mistaken for versioned paths.
- Formulas in LaTeX notation written by the language model (`$\beta = .35$`, `$\ge 21$`) now appear as text (β = .35,
  ≥ 21) in Word, Markdown, copied text, the Zotero note and the app; the LaTeX export keeps them as formulas. The
  writing prompt now asks for plain text with Unicode characters instead.
- "Shin et al. [@KEY]" no longer becomes "Shin et al. (Shin et al. 2021)": if the author is named right before the
  citation, it only shows year and page – in Word via Zotero's "suppress-author", in LaTeX via `\parencite*`.

## 0.9.0 – 2026-10-23

### New
- **Word and LibreOffice documents:** ZotLLM now also reads Word files (.docx) and LibreOffice documents (.odt) stored
  as attachments in Zotero – the main text with headings, lists and tables; footnotes and endnotes follow at the end of
  their page or of the document. For tracked changes, the current state counts; headers, footers, comments and indexes
  are left out. The old Word format (.doc) is not supported.
  - **Page numbers** come from the page breaks Word or LibreOffice recorded when the document was last saved,
    including a different first page number. Without them (e.g. a Google Docs export), citations read "o. S." (n.p.).
  - Word attachments already in your library are picked up at the next sync with Zotero and read in the next indexing
    run; existing settings are extended automatically.
  - Citations of Word documents offer "Show in Zotero" instead of "Open PDF", because Zotero does not display Word files
    itself.

## 0.8.1 – 2026-10-21

First public release.

- **Ask:** questions about your knowledge corpora, answered only from the retrieved passages. Every citation opens the
  passage and its page; "Open PDF" jumps there in Zotero. "Passages only" searches without a language model.
- **Literature review:** an editable search strategy, screening, one evaluation sheet per text (stored as a Zotero
  note), an editable outline, section-by-section writing, and a check of every citation and verbatim quotation against
  the sources. Output as Markdown, Word (live Zotero fields), LaTeX or a Zotero note, with a search-method appendix.
- **Search without a language model** across your library and all groups: copy references and in-text citations in
  your Zotero citation style, export selections as Word, BibTeX, RIS or Markdown, add them to a corpus or a Zotero
  collection, or ask about exactly these items.
- **Projects** bundle corpora, chats and literature reviews; export and import as `.zllm` files, archive.
- **Language models:** Claude, ChatGPT (OpenAI), Gemini, DeepSeek and local models via Ollama. API keys stay in the
  operating system's key store.
- **Built-in embeddings** (llama.cpp) with a curated catalog of 12 models, checksum-verified downloads, a short benchmark
  on your own computer and a comparison mode; Ollama and cloud embeddings are also supported.
- **Indexing for large libraries:** printed page numbers, resumable runs, a queue, background mode in stages, duplicate
  detection across libraries, citations that follow deleted duplicates, backups.
- **Zotero plugin** with context menus; MCP server for Claude Desktop.
- Interface and manual in English and German.
