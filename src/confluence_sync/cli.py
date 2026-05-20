from pathlib import Path

import click
import requests
from rich.console import Console

from confluence_sync.api import ConfluenceClient
from confluence_sync.auth import load_config, save_config, validate_credentials
from confluence_sync.models import FileStatus
from confluence_sync.sync import get_status, pull_space, push_changes

console = Console()


@click.group()
@click.version_option()
def main():
    """Confluence Sync — synkroniser Confluence til lokalt Markdown."""
    pass


@main.command()
def auth():
    """Koble til Confluence Cloud med API-token."""
    console.print(
        "\nOpprett API-token her: [link]https://id.atlassian.com/manage-profile/security/api-tokens[/link]\n"
    )

    instance_url = click.prompt("Atlassian Cloud URL (f.eks. mycompany.atlassian.net)")
    instance_url = instance_url.removeprefix("https://").removeprefix("http://").rstrip("/")

    email = click.prompt("E-post")
    api_token = click.prompt("API-token", hide_input=True)

    console.print("Verifiserer tilkobling...", style="dim")

    try:
        display_name = validate_credentials(instance_url, email, api_token)
    except Exception as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)

    save_config(instance_url, email, api_token)
    console.print(f"[green]Logget inn som {display_name}. Credentials lagret.[/green]")


@main.command()
@click.option("--space", required=True, help="Confluence space key")
@click.option("--output", default=".", help="Output directory")
@click.option("--page-id", default=None, help="Sync specific page and children")
def pull(space, output, page_id):
    """Hent sider fra Confluence og lagre som Markdown."""
    try:
        config = load_config()
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)

    client = ConfluenceClient(
        instance_url=config["instance_url"],
        email=config["email"],
        api_token=config["api_token"],
    )

    console.print(f"Henter sider fra space [bold]{space}[/bold]...")

    try:
        count = pull_space(space, Path(output), client)
    except requests.RequestException as e:
        console.print(f"[red]Feil ved henting fra Confluence:[/red] {e}")
        raise SystemExit(1)

    console.print(f"[green]Ferdig! {count} sider synkronisert.[/green]")


@main.command()
@click.option("--dry-run", is_flag=True, help="Vis hva som ville blitt pushet")
@click.argument("files", nargs=-1)
def push(dry_run, files):
    """Push lokale endringer tilbake til Confluence."""
    try:
        config = load_config()
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)

    client = ConfluenceClient(
        instance_url=config["instance_url"],
        email=config["email"],
        api_token=config["api_token"],
    )

    try:
        results = push_changes(Path("."), client, list(files) or None, dry_run)
    except requests.RequestException as e:
        console.print(f"[red]Feil ved pushing til Confluence:[/red] {e}")
        raise SystemExit(1)

    for item in results:
        if item["status"] == "pushed":
            console.print(f"[green]Pushet:[/green] {item['title']} ({item['file']})")
        elif item["status"] == "skipped":
            console.print(f"[dim]Uendret: {item['title']} ({item['file']})[/dim]")
        elif item["status"] == "dry_run":
            console.print(f"[yellow]Ville pushet:[/yellow] {item['title']} ({item['file']})")

    pushed = sum(1 for r in results if r["status"] == "pushed")
    dry_run_count = sum(1 for r in results if r["status"] == "dry_run")
    skipped = sum(1 for r in results if r["status"] == "skipped")

    if dry_run:
        console.print(f"\n[yellow]{dry_run_count} sider ville blitt pushet[/yellow], {skipped} uendret.")
    else:
        console.print(f"\n[green]{pushed} sider pushet[/green], {skipped} uendret.")


@main.command()
@click.option("--verbose", is_flag=True, help="Vis også uendrede filer")
@click.option("--check-remote", is_flag=True, help="Sjekk remote endringer (krever nett)")
def status(verbose, check_remote):
    """Vis synkroniseringsstatus for lokale filer."""
    from rich.table import Table

    client = None
    if check_remote:
        try:
            config = load_config()
        except FileNotFoundError as e:
            console.print(f"[red]Feil:[/red] {e}")
            raise SystemExit(1)
        client = ConfluenceClient(
            instance_url=config["instance_url"],
            email=config["email"],
            api_token=config["api_token"],
        )

    try:
        results = get_status(Path("."), client)
    except Exception as e:
        console.print(f"[red]Feil ved lesing av status:[/red] {e}")
        raise SystemExit(1)

    _STATUS_STYLE = {
        FileStatus.UNCHANGED: ("green", "unchanged"),
        FileStatus.MODIFIED_LOCAL: ("yellow", "modified_local"),
        FileStatus.MODIFIED_REMOTE: ("blue", "modified_remote"),
        FileStatus.CONFLICT: ("red", "conflict"),
    }

    table = Table(title="Confluence Sync Status")
    table.add_column("Status", style="bold")
    table.add_column("Fil")
    table.add_column("Tittel")

    for item in results:
        file_status: FileStatus = item["status"]
        if file_status == FileStatus.UNCHANGED and not verbose:
            continue
        color, label = _STATUS_STYLE.get(file_status, ("white", file_status.value))
        table.add_row(
            f"[{color}]{label}[/{color}]",
            item["file"],
            item["title"],
        )

    console.print(table)

    modified_local = sum(1 for r in results if r["status"] == FileStatus.MODIFIED_LOCAL)
    modified_remote = sum(1 for r in results if r["status"] == FileStatus.MODIFIED_REMOTE)
    conflicts = sum(1 for r in results if r["status"] == FileStatus.CONFLICT)

    console.print(
        f"[yellow]{modified_local} endret lokalt[/yellow], "
        f"[blue]{modified_remote} endret remote[/blue], "
        f"[red]{conflicts} konflikter[/red]"
    )
