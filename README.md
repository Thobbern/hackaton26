# Confluence Sync

Toveis-synkronisering mellom Confluence og et lokalt Markdown-filhierarki — rediger dokumentasjon i din foretrukne editor og push endringene tilbake til Confluence via API.

## Hva det gjør

Confluence Sync henter et Confluence-space og speiler sidetreet som mapper og Markdown-filer lokalt. Endringer kan pushes tilbake, og verktøyet varsler om konflikter hvis en side er endret både lokalt og i Confluence siden siste sync.

Eksempel på mappestruktur etter `confluence-sync pull --space DEV`:

```
docs/
  DEV/
    Engineering/
      Backend/
        API-design.md
        Databasestruktur.md
      Frontend/
        Komponentbibliotek.md
    Rutiner/
      Onboarding.md
      Deployrutine.md
```

## Installasjon

Krever Python 3.11+ og [uv](https://github.com/astral-sh/uv).

```bash
uv venv && uv pip install -e .
```

## Konfigurasjon

Sett miljøvariabler for autentisering:

```bash
confluence-sync auth
```

Du blir bedt om Confluence-URL, brukernavn og API-token. Konfigurasjon lagres i `~/.confluence-sync/config.yaml`.

## Bruk

### Pull — hent sider fra Confluence

```bash
# Hent hele DEV-spacet
confluence-sync pull --space DEV

# Hent bare én side og dens undersider
confluence-sync pull --page-id 123456
```

### Push — publiser lokale endringer til Confluence

```bash
# Push alle endrede filer
confluence-sync push

# Se hva som ville blitt pushet, uten å gjore noe
confluence-sync push --dry-run
```

### Status — se hvilke filer som har endringer

```bash
# Vis lokale endringer
confluence-sync status

# Sammenlign også med nåværende innhold i Confluence
confluence-sync status --check-remote
```

## Eksempel på generert Markdown-fil

Hver fil har YAML-frontmatter med metadata fra Confluence:

```markdown
---
confluence_id: 8675309
space: DEV
title: API-design
version: 14
parent_id: 8675200
synced_at: "2026-05-20T10:32:00+02:00"
---

# API-design

Her dokumenterer vi REST-API-et for backend-tjenestene...
```

Frontmatter brukes av verktøyet for å spore hvilken Confluence-side filen tilhorer, versjonsnummer for konfliktdeteksjon, og tidspunkt for siste sync.

## Krav

- Python 3.11+
- Atlassian Cloud API-token ([opprett her](https://id.atlassian.com/manage-profile/security/api-tokens))
- Tilgang til et Confluence Cloud-instans
