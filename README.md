# atlassinate

Toveis arbeidsflyt mellom Confluence/Jira og lokal disk. Gir to CLI-er:

- **`gonfluence`** — speil et Confluence-space som Markdown lokalt, rediger
  sider i editoren din via en arbeidskopi-sandboks, og publiser endringene
  tilbake til Confluence med eksplisitt konfliktdeteksjon.
- **`gira`** — administrer Jira-issues fra terminalen (list, vis, opprett,
  kommentér, oppdater).

I tillegg kommer verktøy for søk i den synkede dokumentasjonen: RAG-indeks,
agentisk `ask` mot Claude Code, line-level `blame`, trust-score per side, og
en MCP-server.

## Mental modell

Gonfluence skiller skarpt mellom **mirror** og **edit**:

```
~/.atlassinate/gonfluence/
  <SPACE>/                    ← Mirror — read-only speil av Confluence
    .atlassinate-sync.json    ← Sync-state (versjoner per side)
    Engineering/
      Backend/
        api-design.md
      ...
  .edits/                     ← Arbeidskopi-sandboks
    <PAGE_ID>/                ← Aktiv edit
      api-design.md
    .archive/                 ← Innsendte/forkastede edits
      20260521T101500Z_8675309/
        api-design.md
```

- `gonfluence sync` skriver kun til `<SPACE>/`. Inkrementell som default:
  hopper over sider hvor `version` matcher, fjerner sider som er borte remote.
- `gonfluence edit <page-id>` kopierer den synkede fila inn i `.edits/` og
  åpner $EDITOR. Du kan endre fila så mye du vil — mirror-en blir ikke rørt.
- `gonfluence submit <page-id>` publiserer arbeidskopien. Konflikt detekteres
  ved at remote-versjon har gått foran base-versjonen i frontmatter.
- `gonfluence rebase <page-id>` oppdaterer base til siste remote. Hvis du har
  lokale endringer beholdes de — neste submit overskriver remote.

## Installasjon

Krever Python 3.11+ og [uv](https://github.com/astral-sh/uv).

```bash
uv venv && uv pip install -e .
```

Valgfritt for `index`/`ask --mode rag` og MCP-serveren:

```bash
uv pip install -e ".[rag]"
```

## Konfigurasjon

```bash
gonfluence auth
```

Spør om Atlassian-instans, e-post og API-token og lagrer i
`~/.confluence-sync/config.yaml`. Samme konfigurasjon brukes av `gira`.

API-tokens opprettes på
<https://id.atlassian.com/manage-profile/security/api-tokens>.

## gonfluence

### Sync — speil et space

```bash
# Inkrementell sync av hele DEV-spacet (default)
gonfluence sync --space DEV

# Bare én side og dens barn
gonfluence sync --space DEV --page-id 123456

# Tving full re-pull (ignorer state)
gonfluence sync --space DEV --full

# Egendefinert mappe (default er ~/.atlassinate/gonfluence/<space>/)
gonfluence sync --space DEV --output ./docs/DEV
```

Etter sync har hver side YAML-frontmatter med metadata:

```markdown
---
confluence_id: 8675309
space_key: DEV
title: API-design
version: 14
parent_id: '8675200'
last_synced: '2026-05-20T10:32:00+00:00'
content_hash: 1f3a...
---

# API-design
...
```

### Edit — rediger i editoren din

```bash
# Start eller gjenoppta en edit (åpner $EDITOR)
gonfluence edit 8675309

# Bare opprett arbeidsfila, ikke åpne $EDITOR
gonfluence edit 8675309 --no-editor

# List alle pågående edits
gonfluence edit --list

# Forkast en edit (arkiveres under .edits/.archive/)
gonfluence edit 8675309 --discard
```

Arbeidskopien lever under `~/.atlassinate/gonfluence/.edits/<page-id>/`.
Den overlever sync — mirror-en kan oppdateres uavhengig.

### Submit — publiser til Confluence

```bash
gonfluence submit 8675309
```

- **Ingen endringer:** no-op, edit står urørt.
- **Suksess:** ny versjon pushes til Confluence, mirror-fila og sync-state
  oppdateres, og edit arkiveres til `.edits/.archive/`.
- **Konflikt:** remote har gått foran base-versjonen. Kjør `rebase` først.

### Rebase — hent siste remote til base

```bash
gonfluence rebase 8675309
```

- **Ingen endring:** remote-versjon er allerede base.
- **Ren rebase:** ingen lokale endringer, arbeidsfila erstattes av remote.
- **Med lokale endringer:** base oppdateres, men brukerens body beholdes.
  Neste submit overskriver remote (manuell 3-veis-merge er ikke i scope —
  bruk diff-verktøyet ditt hvis du må).

### Sidekommandoer

```bash
gonfluence page list --space DEV
gonfluence page search --space DEV --query "API"
gonfluence page create --space DEV --title "Ny side" --body "Hei"
gonfluence page delete <PAGE_ID> --confirm
```

### Søk og analyse

`--space DEV` finner automatisk mirror-en under
`~/.atlassinate/gonfluence/DEV/`; alternativt kan du peke direkte med
`--docs <sti>`.

```bash
# RAG-indeks (semantisk søk)
gonfluence index --space DEV

# Spør Claude (agentisk: bruker Read + ripgrep selv)
gonfluence ask --space DEV "Hvordan deployer vi backend?"

# RAG-modus: hent top-K chunks først
gonfluence ask --space DEV --mode rag "API-versjonering?"

# MCP-server for Claude Code
gonfluence mcp --space DEV
```

### Blame og trust

```bash
# Linje-for-linje attribusjon for én side
gonfluence blame ~/.atlassinate/gonfluence/DEV/Engineering/api-design.md

# Pålitelighets-score (recency × doc-type × stabilitet)
gonfluence trust ~/.atlassinate/gonfluence/DEV/Engineering/api-design.md

# Parallell trust-analyse av alle synkede sider
gonfluence trust-all --space DEV --level D,F
```

## gira

Bruker samme konfigurasjon som `gonfluence` — ingen ekstra oppsett.

```bash
# List issues
gira list --project PROJ
gira list --project PROJ --jql "status = 'In Progress' AND assignee = currentUser()"

# Vis et issue
gira show PROJ-123

# Opprett
gira create --project PROJ --summary "Fiks login-bug" --type Bug

# Kommentér
gira comment PROJ-123 "Fikset i commit abc123"

# Oppdater
gira update PROJ-123 --status "In Progress"
gira update PROJ-123 --summary "Ny tittel"
```

## Miljøvariabler

- `ATLASSINATE_HOME` — overstyrer plasseringen av all atlassinate-state
  (default: `~/.atlassinate`).
- `EDITOR` / `VISUAL` — hvilken editor `gonfluence edit` åpner (default: `vi`).

## Krav

- Python 3.11+
- Atlassian Cloud API-token
- Tilgang til et Confluence Cloud-instans
- `rg` (ripgrep) for `gonfluence ask`
