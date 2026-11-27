# Privacy in Zotivity

As of version 1.3.0, 27 November 2026 · [Deutsch](PRIVACY.de.md)

Zotivity runs on your computer. There is no Zotivity account, no usage statistics, no crash reports and no
advertising. Settings, index, knowledge corpora, chats, literature reviews and searches are kept in Zotivity's data
folder (Windows: `%LOCALAPPDATA%\Zotivity`). API keys are stored encrypted in the operating system's credential
store (Windows: Credential Manager).

## Connections you choose

These connections are only made when you set up or use the respective feature. The terms and privacy policy of
each service apply to the data you send there.

- **Zotero on this computer:** Zotivity reads your library through Zotero's local API (127.0.0.1). This data does
  not leave your computer.
- **Language models:** If you set up a provider (e.g. Anthropic, OpenAI, Google Gemini, Mistral or an
  OpenAI-compatible server) and use AI features, your question, the matching passages from your texts and working
  instructions are sent to that provider. With Ollama or the built-in embedding model everything stays on your
  computer.
- **Literature databases:** When you search, your queries (search terms, DOIs) and a stored key, if any, are sent to
  the databases you selected (e.g. PubMed, OpenAlex, Semantic Scholar, Crossref, Europe PMC, ERIC, EconBiz, arXiv,
  OSF, swisscovery). The "Test" button next to a key sends one small request to the service.
- **Zotero web API (api.zotero.org):** only if you store a key for it.
- **Hugging Face:** only when you download or search for an embedding model.
- **Figures:** only if you switch them on under Settings → Index and choose "Describe" (also "Describe the figure and
  answer again" under Ask) or turn on "Describe automatically after each indexing run" – then after every indexing
  run without further confirmation. The caption, text of the page and – for models that understand images – an image
  of the figure are sent to the chosen language model.
- **Citation styles from zotero.org:** only when you fetch a style in the CSL editor.
- **Backup to a folder of your choice:** only if you choose a folder under Settings → Index. If it is a synchronised
  folder (e.g. switchdrive or OneDrive), that program uploads the backup to its cloud. API keys are never in the
  backup.

## The connection Zotivity makes on its own: the update check

At start-up Zotivity asks GitHub whether a new version is available. It downloads the small file
`zotivity-update.json` of the latest release from `github.com/avibecoder42/Zotivity`. Only what every web request
contains is transmitted: your IP address and the identifier "Zotivity/version". No content, names or identifiers
from your library are sent. If you choose "Update now", Zotivity also downloads the setup from GitHub. GitHub's
privacy statement applies.

**To turn it off:**

- during installation: clear the checkbox "Check for updates at start-up";
- later in Zotivity: Settings → Program & data → "Check for updates at start-up".

Without the automatic check you can still use "Check for updates" there at any time.

## Questions

Please open an issue on GitHub: https://github.com/avibecoder42/Zotivity/issues
