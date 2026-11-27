# Datenschutz bei Zotivity

Stand: Version 1.3.0, 27. November 2026 · [English](PRIVACY.md)

Zotivity läuft auf deinem Computer. Es gibt kein Konto bei Zotivity, keine Nutzungsstatistik, keine Absturzberichte
und keine Werbung. Einstellungen, Index, Wissenskorpora, Chats, Forschungsstände und Recherchen liegen im
Datenordner von Zotivity (Windows: `%LOCALAPPDATA%\Zotivity`). API-Schlüssel liegen verschlüsselt im
Schlüsselspeicher des Betriebssystems (Windows: Anmeldeinformationsverwaltung).

## Verbindungen, die du selbst wählst

Diese Verbindungen entstehen nur, wenn du die jeweilige Funktion einrichtest oder benutzt. Für die Daten beim
jeweiligen Dienst gelten dessen Bedingungen und Datenschutzerklärung.

- **Zotero auf diesem Computer:** Zotivity liest deine Bibliothek über die lokale Schnittstelle von Zotero
  (127.0.0.1). Diese Daten verlassen den Computer nicht.
- **Sprachmodelle:** Wenn du einen Anbieter einrichtest (z. B. Anthropic, OpenAI, Google Gemini, Mistral oder einen
  OpenAI-kompatiblen Server) und KI-Funktionen nutzt, gehen deine Frage, die passenden Textstellen aus deinen
  Texten und Arbeitsanweisungen an diesen Anbieter. Mit Ollama oder dem eingebauten Embedding-Modell bleibt alles
  auf dem Computer.
- **Literaturdatenbanken:** Bei einer Recherche gehen deine Suchanfragen (Suchbegriffe, DOIs) und ein allenfalls
  gespeicherter Schlüssel an die gewählten Datenbanken (z. B. PubMed, OpenAlex, Semantic Scholar, Crossref,
  Europe PMC, ERIC, EconBiz, arXiv, OSF, swisscovery). Der Knopf «Testen» bei einem Schlüssel schickt eine kleine
  Probeanfrage an den Dienst.
- **Zotero-Web-API (api.zotero.org):** nur, wenn du dafür einen Schlüssel speicherst.
- **Hugging Face:** nur, wenn du ein Embedding-Modell herunterlädst oder nach einem suchst.
- **Abbildungen:** nur, wenn du sie unter Einstellungen → Index einschaltest und «Beschreiben» wählst. Dann gehen
  Bildunterschrift, Text der Seite und – für Modelle, die Bilder verstehen – ein Bild der Abbildung an das gewählte
  Sprachmodell.
- **Zitierstile von zotero.org:** nur, wenn du im CSL-Editor einen Stil holst.
- **Sicherung in einen eigenen Ordner:** nur, wenn du unter Einstellungen → Index einen Ordner wählst. Liegt er in
  einem synchronisierten Ordner (z. B. switchdrive oder OneDrive), lädt dessen Programm die Sicherung in seine
  Cloud. API-Schlüssel sind nie in der Sicherung.

## Verbindung, die Zotivity von sich aus aufbaut: die Update-Prüfung

Beim Start fragt Zotivity bei GitHub nach, ob es eine neue Version gibt. Dazu lädt es die kleine Datei
`zotivity-update.json` des neuesten Releases von `github.com/avibecoder42/Zotivity`. Übertragen wird dabei nur, was
jede Webanfrage enthält: deine IP-Adresse und die Kennung «Zotivity/Version». Es werden keine Inhalte, Namen oder
Kennungen deiner Bibliothek gesendet. Wählst du «Jetzt aktualisieren», lädt Zotivity das Setup ebenfalls von GitHub.
Es gilt die Datenschutzerklärung von GitHub.

**Abschalten:**

- bei der Installation: Kontrollkästchen «Beim Start nach Updates suchen» abwählen;
- später in Zotivity: Einstellungen → Programm & Daten → «Beim Start nach Updates suchen».

Ohne die automatische Prüfung kannst du dort jederzeit selbst «Nach Updates suchen».

## Fragen

Fragen und Hinweise bitte als Issue auf GitHub: https://github.com/avibecoder42/Zotivity/issues
