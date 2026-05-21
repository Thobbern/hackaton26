from pathlib import Path

import click
import requests
from rich.console import Console
from rich.progress import Progress

from atlassinate.api import ConfluenceClient
from atlassinate.auth import load_config
from atlassinate.cli_common import auth_command
from atlassinate.models import SyncState
from atlassinate.paths import mirror_path
from atlassinate.sync import mirror_space
from atlassinate import edit as edit_mod

console = Console()


def _get_confluence_client():
    config = load_config()
    return ConfluenceClient(
        instance_url=config["instance_url"],
        email=config["email"],
        api_token=config["api_token"],
    )


@click.group(invoke_without_command=True)
@click.version_option()
@click.pass_context
def main(ctx):
    """Gonfluence — synkroniser Confluence til lokalt Markdown."""
    if ctx.invoked_subcommand and ctx.invoked_subcommand != "mcp":
        from atlassinate.banner import print_banner

        print_banner("gonfluence")
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


main.add_command(auth_command)


@main.command()
@click.option("--space", required=True, help="Confluence space key")
@click.option(
    "--output",
    default=None,
    help="Mappa speilet skrives til (default: ~/.atlassinate/gonfluence/<space>/)",
)
@click.option("--page-id", default=None, help="Synk bare denne siden og dens barn")
@click.option(
    "--full",
    is_flag=True,
    help="Tving full re-pull (ignorer lagret state, last alle sider på nytt)",
)
def sync(space, output, page_id, full):
    """Speil et Confluence-space lokalt som Markdown (enveis).

    Default-mappa er ~/.atlassinate/gonfluence/<space>/. Inkrementelt:
    hopper over sider hvor remote `version` matcher state. Sider som er
    fjernet remote, slettes lokalt.
    """
    try:
        client = _get_confluence_client()
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)

    output_path = Path(output).resolve() if output else mirror_path(space)
    console.print(f"[dim]Mappe: {output_path}[/dim]")

    try:
        with Progress(console=console) as progress:
            task = progress.add_task("Henter sider...", total=None)

            def on_page(title, action):
                label = {"pulled": "Hentet", "skipped": "Uendret", "removed": "Fjernet"}.get(action, action)
                progress.update(task, advance=1, description=f"{label}: {title}")

            result = mirror_space(
                space,
                output_path,
                client,
                page_id=page_id,
                incremental=not full,
                progress_callback=on_page,
            )
    except requests.RequestException as e:
        console.print(f"[red]Feil ved henting fra Confluence:[/red] {e}")
        raise SystemExit(1)

    console.print(
        f"[green]Ferdig![/green] "
        f"{result['pulled']} hentet, {result['skipped']} uendret, "
        f"{result['removed']} fjernet."
    )


# Skjult bak-kompat-alias for tidligere `pull`-kommando.
@main.command(hidden=True)
@click.option("--space", required=True)
@click.option("--output", default=None)
@click.option("--page-id", default=None)
@click.option("--full", is_flag=True)
@click.pass_context
def pull(ctx, space, output, page_id, full):
    """Deprecated: bruk `gonfluence sync` i stedet."""
    console.print("[yellow]`pull` er erstattet av `sync`.[/yellow]")
    ctx.invoke(sync, space=space, output=output, page_id=page_id, full=full)


def _format_edit_row(entry) -> tuple[str, str, str, str]:
    state = "endret" if entry.modified else "ren"
    color = "yellow" if entry.modified else "green"
    return (
        f"[{color}]{state}[/{color}]",
        entry.page_id,
        entry.title,
        str(entry.filepath),
    )


@main.command()
@click.argument("page_id", required=False)
@click.option("--list", "list_only", is_flag=True, help="List pågående edits")
@click.option("--discard", is_flag=True, help="Forkast edit-en for PAGE_ID")
@click.option(
    "--no-editor",
    is_flag=True,
    help="Ikke åpne $EDITOR — bare lag/oppdater arbeidskopien",
)
def edit(page_id, list_only, discard, no_editor):
    """Rediger en synket Confluence-side i $EDITOR.

    Arbeidskopien lever under `~/.atlassinate/gonfluence/.edits/<page_id>/`
    og kan endres så mange ganger som ønsket. `gonfluence submit <page-id>`
    publiserer endringene til Confluence; `gonfluence rebase <page-id>`
    oppdaterer base til siste remote.
    """
    import os
    import subprocess
    from rich.table import Table

    if list_only:
        entries = edit_mod.list_edits()
        if not entries:
            console.print("[dim]Ingen pågående edits.[/dim]")
            return
        table = Table(title="Pågående edits")
        table.add_column("Status", style="bold")
        table.add_column("Page ID", style="dim")
        table.add_column("Tittel")
        table.add_column("Fil", style="dim")
        for entry in entries:
            table.add_row(*_format_edit_row(entry))
        console.print(table)
        return

    if not page_id:
        console.print("[red]Feil:[/red] PAGE_ID kreves (eller bruk --list).")
        raise SystemExit(1)

    if discard:
        try:
            archived = edit_mod.discard_edit(page_id)
        except FileNotFoundError as e:
            console.print(f"[red]Feil:[/red] {e}")
            raise SystemExit(1)
        console.print(f"[green]Forkastet[/green] edit for {page_id} → {archived}")
        return

    try:
        target = edit_mod.start_edit(page_id)
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)

    console.print(f"[dim]Edit-fil: {target}[/dim]")

    if no_editor:
        return

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    try:
        subprocess.run([editor, str(target)], check=False)
    except FileNotFoundError:
        console.print(
            f"[yellow]Kunne ikke åpne `{editor}`. Sett $EDITOR eller rediger "
            f"fila direkte:[/yellow] {target}"
        )


@main.command()
@click.argument("page_id")
def submit(page_id):
    """Publiser en pågående edit til Confluence."""
    try:
        client = _get_confluence_client()
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)

    try:
        result = edit_mod.submit_edit(page_id, client)
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)
    except requests.RequestException as e:
        console.print(f"[red]Feil ved publisering til Confluence:[/red] {e}")
        raise SystemExit(1)

    status = result["status"]
    title = result.get("title", page_id)

    if status == "unchanged":
        console.print(f"[dim]Ingen endringer å publisere for {title}.[/dim]")
    elif status == "submitted":
        console.print(
            f"[green]Publisert:[/green] {title} → v{result['new_version']}"
        )
        console.print(f"[dim]Edit arkivert: {result['archived_at']}[/dim]")
    elif status == "conflict":
        console.print(
            f"[red]Konflikt:[/red] {title} har gått fra "
            f"v{result['base_version']} til v{result['remote_version']} remote. "
            f"Kjør `gonfluence rebase {page_id}` først."
        )
        raise SystemExit(2)
    else:
        console.print(f"[yellow]Ukjent status: {status}[/yellow]")


@main.command()
@click.argument("page_id")
def rebase(page_id):
    """Hent siste remote-versjon og oppdater edit-ens base."""
    try:
        client = _get_confluence_client()
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)

    try:
        result = edit_mod.rebase_edit(page_id, client)
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)
    except requests.RequestException as e:
        console.print(f"[red]Feil ved henting fra Confluence:[/red] {e}")
        raise SystemExit(1)

    status = result["status"]
    title = result.get("title", page_id)

    if status == "noop":
        console.print(f"[dim]Allerede oppdatert: {title} står på siste remote-versjon.[/dim]")
    elif status == "rebased_clean":
        console.print(
            f"[green]Rebaset[/green] {title} til v{result['new_base_version']} "
            f"(ingen lokale endringer)."
        )
    elif status == "rebased_with_local_changes":
        console.print(
            f"[yellow]Rebaset med lokale endringer:[/yellow] {title} "
            f"v{result['previous_base_version']} → v{result['new_base_version']}. "
            f"Neste submit overskriver remote."
        )
    else:
        console.print(f"[yellow]Ukjent status: {status}[/yellow]")


@main.group()
def page():
    """Confluence sidekommandoer."""
    pass


@page.command("list")
@click.option("--space", required=True, help="Confluence space key")
def page_list(space):
    """Vis alle sider i et Confluence-space."""
    from rich.table import Table

    try:
        client = _get_confluence_client()
        pages = client.list_pages(space)
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)
    except requests.RequestException as e:
        console.print(f"[red]Feil ved henting fra Confluence:[/red] {e}")
        raise SystemExit(1)

    table = Table(title=f"Sider i space: {space}")
    table.add_column("ID", style="dim")
    table.add_column("Tittel", style="bold")
    table.add_column("Parent ID", style="dim")

    for p in pages:
        table.add_row(str(p.get("id", "")), p.get("title", ""), str(p.get("parentId") or ""))

    console.print(table)


@page.command("search")
@click.option("--space", required=True, help="Confluence space key")
@click.option("--query", required=True, help="Søketekst")
def page_search(space, query):
    """Søk etter sider i et Confluence-space."""
    from rich.table import Table

    try:
        client = _get_confluence_client()
        results = client.search_pages(space, query)
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)
    except requests.RequestException as e:
        console.print(f"[red]Feil ved søk i Confluence:[/red] {e}")
        raise SystemExit(1)

    table = Table(title=f'Søkeresultater for "{query}" i {space}')
    table.add_column("ID", style="dim")
    table.add_column("Tittel", style="bold")
    table.add_column("Space")

    for item in results:
        content = item.get("content") or item
        page_id = str(content.get("id", ""))
        title = content.get("title", item.get("title", ""))
        space_name = (content.get("space") or {}).get("key", space)
        table.add_row(page_id, title, space_name)

    console.print(table)
    console.print(f"[dim]{len(results)} resultat(er) funnet[/dim]")


@page.command("create")
@click.option("--space", required=True, help="Confluence space key")
@click.option("--title", required=True, help="Sidetittel")
@click.option("--parent-id", default=None, help="Parent page ID")
@click.option("--body", "body_text", default="", help="Sideinnhold (Markdown)")
def page_create(space, title, parent_id, body_text):
    """Opprett en ny side i Confluence."""
    from atlassinate.converter import markdown_to_storage

    storage_body = markdown_to_storage(body_text) if body_text else ""

    try:
        client = _get_confluence_client()
        created = client.create_page(space, title, storage_body, parent_id)
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)
    except requests.RequestException as e:
        console.print(f"[red]Feil ved oppretting av side:[/red] {e}")
        raise SystemExit(1)

    page_id = created.get("id", "")
    page_title = created.get("title", title)
    console.print(f"[green]Side opprettet — ID: {page_id}, tittel: {page_title}[/green]")


@page.command("delete")
@click.argument("page_id")
@click.option("--confirm", is_flag=True, default=False, help="Bekreft sletting")
def page_delete(page_id, confirm):
    """Slett en Confluence-side."""
    if not confirm:
        console.print("[red]Advarsel:[/red] Bruk --confirm for å slette")
        raise SystemExit(1)

    try:
        client = _get_confluence_client()
        client.delete_page(page_id)
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)
    except requests.RequestException as e:
        console.print(f"[red]Feil ved sletting av side:[/red] {e}")
        raise SystemExit(1)

    console.print(f"[green]Side {page_id} slettet.[/green]")


ASK_SYSTEM_PROMPT = (
    "Du er en assistent som svarer på spørsmål om Confluence-dokumentasjon "
    "lagret som Markdown-filer med YAML-frontmatter (felter: confluence_id, "
    "space, title, version, parent_id, synced_at). Bruk ripgrep (`rg`) via "
    "Bash for å finne relevante filer, og Read for å lese dem. Svar konsist "
    "på norsk og siter alltid kildefil(er) med relativ sti. Hvis du ikke "
    "finner svaret i dokumentasjonen, si det eksplisitt i stedet for å gjette."
)

RAG_SYSTEM_PROMPT_TEMPLATE = (
    "Du er en assistent som svarer på spørsmål om Confluence-dokumentasjon. "
    "Du får under en liste med RELEVANTE UTDRAG hentet via semantisk søk. "
    "Svar primært basert på disse utdragene. Du kan også bruke Read for å "
    "lese hele filer, eller ripgrep (`rg`) via Bash for å lete videre, "
    "dersom utdragene ikke gir nok kontekst. Svar konsist på norsk og siter "
    "alltid kildefil(er) med relativ sti. Hvis svaret ikke finnes i "
    "utdragene eller filene, si det eksplisitt.\n\n"
    "RELEVANTE UTDRAG:\n{context}"
)


def _resolve_db_path(docs_path: Path) -> Path:
    from atlassinate.rag import DEFAULT_DB_NAME

    return docs_path / DEFAULT_DB_NAME


def _resolve_docs(docs: str | None, space: str | None) -> Path:
    """Bestem hvilken dokumentasjonsmappe en kommando skal jobbe mot.

    Eksplisitt `--docs` vinner. Ellers `--space` → mirror under
    `~/.atlassinate/gonfluence/<space>/`. Faller tilbake til cwd hvis
    ingen er gitt (bak-kompat).
    """
    from atlassinate.paths import mirror_path

    if docs is not None:
        return Path(docs).resolve()
    if space:
        return mirror_path(space).resolve()
    return Path(".").resolve()


@main.command()
@click.option(
    "--docs",
    default=None,
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Mappe med markdown-dokumentasjon (default: mirror for --space, ellers cwd)",
)
@click.option(
    "--space",
    default=None,
    help="Space-key for mirror under ~/.atlassinate/gonfluence/<space>/",
)
@click.option(
    "--model",
    default=None,
    help="Sentence-transformers modellnavn (default: multilingual-e5-small)",
)
def index(docs, space, model):
    """Bygg/oppdater RAG-indeks for synkede sider (semantisk søk)."""
    from atlassinate.rag import (
        DEFAULT_MODEL,
        RagDependencyError,
        build_index,
        index_stats,
    )

    docs_path = _resolve_docs(docs, space)
    db_path = _resolve_db_path(docs_path)
    model_name = model or DEFAULT_MODEL

    try:
        with Progress(console=console) as progress:
            task = progress.add_task(
                f"Indekserer med {model_name} (laster modell ved første kjøring)...",
                total=None,
            )

            def on_page(title):
                progress.update(task, advance=1, description=f"Indeksert: {title}")

            result = build_index(
                docs_path, db_path, model_name=model_name, progress_callback=on_page
            )
    except RagDependencyError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)
    except RuntimeError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)

    stats = index_stats(db_path) or {}
    console.print(
        f"[green]Ferdig![/green] "
        f"{result['new']} nye, {result['updated']} oppdatert, "
        f"{result['unchanged']} uendret, {result['removed']} fjernet. "
        f"[dim]Totalt {stats.get('pages', 0)} sider / "
        f"{stats.get('chunks', 0)} chunks i {db_path.name}.[/dim]"
    )


@main.command()
@click.option(
    "--docs",
    default=None,
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Mappe med markdown-dokumentasjon (default: mirror for --space, ellers cwd)",
)
@click.option(
    "--space",
    default=None,
    help="Space-key for mirror under ~/.atlassinate/gonfluence/<space>/",
)
def mcp(docs, space):
    """Start MCP-server som gir Claude Code tilgang til search_docs-verktøyet.

    Registrer i Claude Code med:
      claude mcp add gonfluence -- gonfluence mcp --docs <absolutt-sti>
    """
    from atlassinate.mcp_server import run_stdio

    docs_path = _resolve_docs(docs, space)
    # NB: ikke skriv til stdout her — det ville korrupt MCP-protokollen.
    run_stdio(docs_path)


_BLAME_COLORS = [
    "cyan", "magenta", "green", "yellow", "blue",
    "bright_cyan", "bright_magenta", "bright_green", "bright_yellow", "bright_blue",
]


def _color_for_author(author_id: str, palette: dict) -> str:
    if author_id not in palette:
        palette[author_id] = _BLAME_COLORS[len(palette) % len(_BLAME_COLORS)]
    return palette[author_id]


def _format_date(iso: str) -> str:
    return iso[:10] if iso else "????-??-??"


@main.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--summary",
    is_flag=True,
    help="Vis kun statistikk per forfatter, ikke linje-for-linje",
)
@click.option(
    "--since",
    default=None,
    help="Vis kun linjer endret på eller etter dato (YYYY-MM-DD)",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output som JSON (for scripting)",
)
@click.option(
    "--refresh",
    is_flag=True,
    help="Ignorer cache og hent versjoner på nytt",
)
def blame(file, summary, since, as_json, refresh):
    """Vis line-level blame for en synket Confluence-side."""
    import json as json_module

    from atlassinate.blame import (
        author_summary,
        clear_cache,
        compute_blame,
        filter_since,
    )
    from atlassinate.frontmatter import read_frontmatter

    filepath = Path(file).resolve()
    try:
        meta, _body = read_frontmatter(filepath)
    except Exception as e:
        console.print(f"[red]Feil:[/red] Kunne ikke lese frontmatter fra {filepath}: {e}")
        raise SystemExit(1)

    try:
        client = _get_confluence_client()
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)

    # Cache ligger ved siden av synkede filer; finn rot via state-fila.
    docs_root = filepath.parent
    while docs_root != docs_root.parent:
        if SyncState.state_file_present(docs_root):
            break
        docs_root = docs_root.parent
    else:
        docs_root = filepath.parent

    if refresh:
        clear_cache(docs_root, meta.confluence_id)

    try:
        with Progress(console=console, transient=True) as progress:
            task = progress.add_task("Henter versjoner...", total=None)

            def on_version(idx, total):
                progress.update(
                    task,
                    total=total,
                    completed=idx,
                    description=f"Versjon {idx}/{total}",
                )

            blame_lines = compute_blame(
                meta.confluence_id, client, docs_root, progress_callback=on_version
            )
    except requests.RequestException as e:
        console.print(f"[red]Feil ved henting fra Confluence:[/red] {e}")
        raise SystemExit(1)

    if since:
        blame_lines = filter_since(blame_lines, since)

    if as_json:
        payload = [
            {
                "line": bl.line,
                "version": bl.attribution.version,
                "author_id": bl.attribution.author_id,
                "author_name": bl.attribution.author_name,
                "created_at": bl.attribution.created_at,
            }
            for bl in blame_lines
        ]
        console.print_json(json_module.dumps(payload))
        return

    if summary or not blame_lines:
        from rich.table import Table

        table = Table(title=f"Bidragsytere — {meta.title}")
        table.add_column("Forfatter", style="bold")
        table.add_column("Linjer", justify="right")
        table.add_column("Andel", justify="right")
        table.add_column("Siste bidrag")

        total_lines = sum(1 for _ in blame_lines) or 1
        for entry in author_summary(blame_lines):
            pct = 100.0 * entry["lines"] / total_lines
            table.add_row(
                entry["author_name"],
                str(entry["lines"]),
                f"{pct:.1f}%",
                _format_date(entry["latest_at"]),
            )
        console.print(table)
        if not blame_lines:
            console.print("[dim]Ingen linjer å vise.[/dim]")
        return

    palette: dict = {}
    for bl in blame_lines:
        color = _color_for_author(bl.attribution.author_id, palette)
        prefix = (
            f"[{color}]{bl.attribution.author_name[:18]:<18}[/{color}] "
            f"[dim]{_format_date(bl.attribution.created_at)}[/dim] "
            f"[dim]v{bl.attribution.version:>3}[/dim] │ "
        )
        console.print(prefix + bl.line, highlight=False)


def _bar(value: float, width: int = 10) -> str:
    filled = max(0, min(width, int(round(value * width))))
    return "█" * filled + "░" * (width - filled)


def _level_color(level: str) -> str:
    return {
        "A": "bright_green",
        "B": "green",
        "C": "yellow",
        "D": "bright_red",
        "F": "red",
    }.get(level, "white")


@main.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output som JSON (for scripting)",
)
@click.option(
    "--refresh",
    is_flag=True,
    help="Ignorer blame-cache og hent versjoner på nytt",
)
def trust(file, as_json, refresh):
    """Beregn pålitelighets-score for en synket Confluence-side."""
    import json as json_module

    from atlassinate.blame import clear_cache, compute_blame
    from atlassinate.frontmatter import read_frontmatter
    from atlassinate.trust import TrustConfig, compute_trust

    filepath = Path(file).resolve()
    try:
        meta, _body = read_frontmatter(filepath)
    except Exception as e:
        console.print(f"[red]Feil:[/red] Kunne ikke lese frontmatter: {e}")
        raise SystemExit(1)

    try:
        client = _get_confluence_client()
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)

    docs_root = filepath.parent
    while docs_root != docs_root.parent:
        if (docs_root / ".confluence-sync.json").exists():
            break
        docs_root = docs_root.parent
    else:
        docs_root = filepath.parent

    if refresh:
        clear_cache(docs_root, meta.confluence_id)

    try:
        with Progress(console=console, transient=True) as progress:
            task = progress.add_task("Henter versjoner...", total=None)

            def on_version(idx, total):
                progress.update(
                    task, total=total, completed=idx,
                    description=f"Versjon {idx}/{total}",
                )

            blame_lines = compute_blame(
                meta.confluence_id, client, docs_root, progress_callback=on_version
            )
    except requests.RequestException as e:
        console.print(f"[red]Feil ved henting fra Confluence:[/red] {e}")
        raise SystemExit(1)

    versions = client.get_page_versions(meta.confluence_id)
    config = TrustConfig.load(docs_root)
    score = compute_trust(meta.title, blame_lines, versions, config)

    if as_json:
        payload = {
            "file": str(filepath.relative_to(docs_root)) if filepath.is_relative_to(docs_root) else str(filepath),
            "title": meta.title,
            "confluence_id": meta.confluence_id,
            "total": round(score.total, 3),
            "level": score.level,
            "components": {
                "recency": round(score.components.recency, 3),
                "doc_type": round(score.components.doc_type, 3),
                "stability": round(score.components.stability, 3),
            },
            "flags": score.flags,
            "stats": {
                "line_count": score.stats["line_count"],
                "version_count": score.stats["version_count"],
                "unique_editors": score.stats["unique_editors"],
                "latest_update": score.stats["latest_update"],
                "matched_type_pattern": score.stats["matched_type_pattern"],
            },
        }
        console.print_json(json_module.dumps(payload))
        return

    color = _level_color(score.level)
    console.print(
        f"\n[bold]{meta.title}[/bold]"
    )
    console.print(
        f"Trust: [{color} bold]{score.level}[/{color} bold] "
        f"([{color}]{score.total:.2f}[/{color}])\n"
    )

    weights = config.weights
    rows = [
        ("Recency", score.components.recency, weights.get("recency", 0.5)),
        ("Doc-type", score.components.doc_type, weights.get("doc_type", 0.2)),
        ("Stabilitet", score.components.stability, weights.get("stability", 0.3)),
    ]
    for label, value, weight in rows:
        console.print(
            f"  {label:<12} {value:.2f}  [dim]{_bar(value)}[/dim]  "
            f"[dim](vekt {weight})[/dim]"
        )

    console.print(
        f"\n[dim]{score.stats['line_count']} linjer, "
        f"{score.stats['version_count']} versjoner, "
        f"{score.stats['unique_editors']} unike redaktører, "
        f"sist endret {score.stats['latest_update'][:10] or '????'}[/dim]"
    )

    if score.flags:
        console.print("\n[bold yellow]Risiko-flagg:[/bold yellow]")
        for flag in score.flags:
            console.print(f"  ⚠ {flag}")
    console.print()


@main.command("trust-all")
@click.option(
    "--docs",
    default=None,
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Mappe med markdown-dokumentasjon (default: mirror for --space, ellers cwd)",
)
@click.option(
    "--space",
    default=None,
    help="Space-key for mirror under ~/.atlassinate/gonfluence/<space>/",
)
@click.option(
    "--pattern",
    default="*.md",
    show_default=True,
    help="Glob-mønster for filer å analysere (relativt til --docs, rekursivt)",
)
@click.option(
    "--level",
    default=None,
    help="Filtrer på letter-grade(s), komma-separert (eks: D,F)",
)
@click.option("--min", "min_score", type=float, default=None, help="Kun score >= min")
@click.option("--max", "max_score", type=float, default=None, help="Kun score <= max")
@click.option("--limit", type=int, default=None, help="Vis kun N filer")
@click.option(
    "--workers",
    type=int,
    default=8,
    show_default=True,
    help="Antall parallelle tråder mot Confluence",
)
@click.option(
    "--sort",
    type=click.Choice(["asc", "desc"]),
    default="asc",
    show_default=True,
    help="Sortering på score (asc = laveste først)",
)
@click.option("--refresh", is_flag=True, help="Ignorer trust-cache og reberegn alt")
@click.option("--json", "as_json", is_flag=True, help="Output som JSON")
def trust_all(docs, space, pattern, level, min_score, max_score, limit, workers, sort, refresh, as_json):
    """Kjør trust-analyse parallelt over alle synkede filer.

    Bruker frontmatter-versjonen for å hoppe over uendrede sider via
    .gonfluence-trust-cache.json. Første kjøring er treg (henter
    versjons-historikk per side); påfølgende kjøringer er øyeblikkelige.
    """
    import json as json_module
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime, timezone

    from atlassinate.blame import compute_blame
    from atlassinate.frontmatter import read_frontmatter
    from atlassinate.trust import (
        TrustConfig,
        cache_is_fresh,
        compute_trust,
        load_trust_cache,
        save_trust_cache,
        score_to_dict,
    )

    docs_path = _resolve_docs(docs, space)

    targets: list[tuple[Path, object]] = []
    for filepath in sorted(docs_path.rglob(pattern)):
        if not filepath.is_file():
            continue
        try:
            meta, _body = read_frontmatter(filepath)
        except Exception:
            continue
        if not getattr(meta, "confluence_id", None):
            continue
        targets.append((filepath, meta))

    if not targets:
        console.print(
            "[yellow]Ingen synkede filer funnet under "
            f"{docs_path} (mønster: {pattern}).[/yellow]"
        )
        return

    try:
        client = _get_confluence_client()
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)

    config = TrustConfig.load(docs_path)
    cache = {} if refresh else load_trust_cache(docs_path)

    def process(filepath: Path, meta) -> tuple[Path, object, dict, bool]:
        cached = cache.get(meta.confluence_id)
        if cache_is_fresh(cached, meta.version):
            return filepath, meta, cached, True
        blame_lines = compute_blame(meta.confluence_id, client, docs_path)
        versions = client.get_page_versions(meta.confluence_id)
        score = compute_trust(meta.title, blame_lines, versions, config)
        entry = {
            "version": meta.version,
            **score_to_dict(score),
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }
        return filepath, meta, entry, False

    all_results: list[tuple[Path, object, dict]] = []
    errors: list[tuple[Path, str]] = []

    with Progress(console=console) as progress:
        task = progress.add_task(
            f"Analyserer {len(targets)} sider ({workers} parallelle)...",
            total=len(targets),
        )
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(process, fp, m): (fp, m) for fp, m in targets
            }
            for future in as_completed(futures):
                fp_orig, _m_orig = futures[future]
                try:
                    filepath, meta, entry, _was_cached = future.result()
                except Exception as e:
                    errors.append((fp_orig, str(e)))
                    progress.update(task, advance=1)
                    continue
                cache[meta.confluence_id] = entry
                all_results.append((filepath, meta, entry))
                progress.update(task, advance=1)

    save_trust_cache(docs_path, cache)

    if errors:
        console.print(
            f"[yellow]{len(errors)} sider feilet:[/yellow] "
            + ", ".join(fp.name for fp, _ in errors[:5])
            + (" ..." if len(errors) > 5 else "")
        )

    filtered = list(all_results)
    if level:
        wanted = {part.strip().upper() for part in level.split(",") if part.strip()}
        filtered = [r for r in filtered if r[2]["level"] in wanted]
    if min_score is not None:
        filtered = [r for r in filtered if r[2]["total"] >= min_score]
    if max_score is not None:
        filtered = [r for r in filtered if r[2]["total"] <= max_score]

    filtered.sort(key=lambda r: r[2]["total"], reverse=(sort == "desc"))
    if limit:
        filtered = filtered[:limit]

    if as_json:
        payload = [
            {
                "file": str(
                    fp.relative_to(docs_path) if fp.is_relative_to(docs_path) else fp
                ),
                "title": meta.title,
                "confluence_id": meta.confluence_id,
                **{k: v for k, v in entry.items() if k != "version"},
                "frontmatter_version": entry.get("version"),
            }
            for fp, meta, entry in filtered
        ]
        console.print_json(json_module.dumps(payload, ensure_ascii=False))
        return

    from rich.table import Table

    table = Table(title=f"Trust-analyse ({len(filtered)} av {len(all_results)} sider)")
    table.add_column("Lvl", style="bold")
    table.add_column("Score", justify="right")
    table.add_column("Fil")
    table.add_column("L", justify="right", style="dim")
    table.add_column("V", justify="right", style="dim")
    table.add_column("Sist endret", style="dim")
    table.add_column("Flagg", style="yellow")

    for fp, meta, entry in filtered:
        color = _level_color(entry["level"])
        rel = (
            fp.relative_to(docs_path) if fp.is_relative_to(docs_path) else fp
        )
        stats = entry.get("stats", {})
        flags = entry.get("flags", [])
        flag_text = flags[0] if flags else ""
        if len(flag_text) > 55:
            flag_text = flag_text[:52] + "..."
        if len(flags) > 1:
            flag_text += f" (+{len(flags) - 1})"

        table.add_row(
            f"[{color}]{entry['level']}[/{color}]",
            f"{entry['total']:.2f}",
            str(rel),
            str(stats.get("line_count", "")),
            str(stats.get("version_count", "")),
            (stats.get("latest_update") or "")[:10] or "????",
            flag_text,
        )

    console.print(table)

    if all_results:
        totals = [e["total"] for _, _, e in all_results]
        avg = sum(totals) / len(totals)
        sorted_totals = sorted(totals)
        median = sorted_totals[len(sorted_totals) // 2]

        level_counts: dict[str, int] = {}
        for _, _, e in all_results:
            level_counts[e["level"]] = level_counts.get(e["level"], 0) + 1

        console.print(
            f"\n[bold]Sammendrag:[/bold] {len(all_results)} sider analysert  │  "
            f"snitt [bold]{avg:.2f}[/bold]  │  median [bold]{median:.2f}[/bold]"
        )

        max_count = max(level_counts.values()) if level_counts else 1
        for letter in ("A", "B", "C", "D", "F"):
            count = level_counts.get(letter, 0)
            pct = 100.0 * count / len(all_results)
            bar_width = int(round(20 * count / max_count)) if count else 0
            bar = "█" * bar_width + "░" * (20 - bar_width)
            color = _level_color(letter)
            console.print(
                f"  [{color}]{letter}[/{color}]: {count:>4} ({pct:>4.1f}%) "
                f"[dim]{bar}[/dim]"
            )


@main.command()
@click.argument("question")
@click.option(
    "--docs",
    default=None,
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Mappe med markdown-dokumentasjon (default: mirror for --space, ellers cwd)",
)
@click.option(
    "--space",
    default=None,
    help="Space-key for mirror under ~/.atlassinate/gonfluence/<space>/",
)
@click.option(
    "--mode",
    type=click.Choice(["agentic", "rag"]),
    default="agentic",
    help="agentic: la Claude bruke Grep/Read selv. rag: hent top-K chunks først.",
)
@click.option(
    "--top-k",
    default=5,
    show_default=True,
    help="Antall chunks å hente i rag-modus.",
)
def ask(question, docs, space, mode, top_k):
    """Spør Claude om dokumentasjonen via Claude Code-abonnementet ditt."""
    import shutil
    import subprocess

    claude_bin = shutil.which("claude")
    if not claude_bin:
        console.print(
            "[red]Feil:[/red] `claude` CLI ikke funnet. "
            "Installer Claude Code og kjør `claude /login` først."
        )
        raise SystemExit(1)

    if not shutil.which("rg"):
        console.print(
            "[red]Feil:[/red] `rg` (ripgrep) ikke funnet. "
            "Installer med `brew install ripgrep` (macOS) eller tilsvarende."
        )
        raise SystemExit(1)

    docs_path = _resolve_docs(docs, space)

    if mode == "rag":
        from atlassinate.rag import RagDependencyError, search

        db_path = _resolve_db_path(docs_path)
        try:
            hits = search(question, db_path, top_k=top_k)
        except FileNotFoundError as e:
            console.print(f"[red]Feil:[/red] {e}")
            raise SystemExit(1)
        except RagDependencyError as e:
            console.print(f"[red]Feil:[/red] {e}")
            raise SystemExit(1)

        if not hits:
            console.print(
                "[yellow]Ingen treff i RAG-indeksen. "
                "Faller tilbake til agentisk modus.[/yellow]"
            )
            system_prompt = ASK_SYSTEM_PROMPT
        else:
            console.print(f"[dim]Top {len(hits)} relevante chunks:[/dim]")
            for h in hits:
                console.print(
                    f"  [dim]{h.score:.3f}[/dim]  [bold]{h.title}[/bold] "
                    f"[dim]({h.file_path} #{h.chunk_idx})[/dim]"
                )
            console.print()

            context_parts = [
                f"--- KILDE: {h.file_path} | TITTEL: {h.title} "
                f"| SCORE: {h.score:.3f} ---\n{h.text}"
                for h in hits
            ]
            system_prompt = RAG_SYSTEM_PROMPT_TEMPLATE.format(
                context="\n\n".join(context_parts)
            )
    else:
        system_prompt = ASK_SYSTEM_PROMPT

    cmd = [
        claude_bin,
        "-p",
        question,
        "--add-dir",
        str(docs_path),
        "--allowed-tools",
        "Read Glob Bash(rg:*)",
        "--append-system-prompt",
        system_prompt,
        "--permission-mode",
        "acceptEdits",
    ]

    console.print(f"[dim]Spør Claude ({mode}) om: {docs_path}[/dim]\n")

    try:
        result = subprocess.run(cmd, cwd=str(docs_path))
    except KeyboardInterrupt:
        raise SystemExit(130)

    if result.returncode != 0:
        raise SystemExit(result.returncode)
