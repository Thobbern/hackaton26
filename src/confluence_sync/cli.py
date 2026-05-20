from pathlib import Path

import click
import requests
from rich.console import Console
from rich.progress import Progress

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

    try:
        with Progress(console=console) as progress:
            task = progress.add_task("Henter sider...", total=None)

            def on_page(title):
                progress.update(task, advance=1, description=f"Hentet: {title}")

            count = pull_space(space, Path(output), client, page_id=page_id, progress_callback=on_page)
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

    if not (Path(".") / ".confluence-sync.json").exists():
        console.print(
            "[yellow]Ingen synk-data funnet. Kjør 'confluence-sync pull --space <KEY>' først.[/yellow]"
        )
        raise SystemExit(1)

    client = ConfluenceClient(
        instance_url=config["instance_url"],
        email=config["email"],
        api_token=config["api_token"],
    )

    results = []
    try:
        with Progress(console=console) as progress:
            task = progress.add_task("Pusher sider...", total=None)
            pushed_count = 0
            skipped_count = 0

            def _do_push():
                nonlocal pushed_count, skipped_count
                for item in push_changes(Path("."), client, list(files) or None, dry_run):
                    results.append(item)
                    if item["status"] == "pushed":
                        pushed_count += 1
                        progress.update(task, advance=1, description=f"Pushet: {item['title']} ({pushed_count} pushet, {skipped_count} uendret)")
                    elif item["status"] == "skipped":
                        skipped_count += 1
                        progress.update(task, advance=1, description=f"Uendret: {item['title']} ({pushed_count} pushet, {skipped_count} uendret)")
                    elif item["status"] == "dry_run":
                        progress.update(task, advance=1, description=f"Ville pushet: {item['title']}")

            _do_push()
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

    if not (Path(".") / ".confluence-sync.json").exists():
        console.print(
            "[yellow]Ingen synk-data funnet. Kjør 'confluence-sync pull --space <KEY>' først.[/yellow]"
        )
        raise SystemExit(1)

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


# ---------------------------------------------------------------------------
# Jira subgroup
# ---------------------------------------------------------------------------


def _get_jira_client():
    config = load_config()
    from confluence_sync.jira_api import JiraClient
    return JiraClient(config["instance_url"], config["email"], config["api_token"])


def _adf_to_text(adf: dict | None) -> str:
    if not adf:
        return ""
    texts = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                texts.append(node.get("text", ""))
            for child in node.get("content", []):
                walk(child)

    walk(adf)
    return " ".join(texts)


@main.group()
def jira():
    """Jira-kommandoer for issues, kommentarer og oppdateringer."""
    pass


@jira.command("list")
@click.option("--project", required=True, help="Jira project key")
@click.option("--jql", default=None, help="Custom JQL query")
@click.option("--limit", default=20, help="Max results")
def jira_list(project, jql, limit):
    """List issues in a Jira project."""
    from rich.table import Table

    if jql is None:
        jql = f"project = {project} ORDER BY updated DESC"

    try:
        client = _get_jira_client()
        issues = client.search_issues(jql, max_results=limit)
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)
    except requests.RequestException as e:
        console.print(f"[red]Feil ved henting fra Jira:[/red] {e}")
        raise SystemExit(1)

    table = Table(title=f"Jira-issues: {project}")
    table.add_column("Key", style="bold")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Prioritet")
    table.add_column("Summary")

    for issue in issues:
        fields = issue.get("fields", {})
        key = issue.get("key", "")
        issue_type = (fields.get("issuetype") or {}).get("name", "")
        status = (fields.get("status") or {}).get("name", "")
        priority = (fields.get("priority") or {}).get("name", "")
        summary = fields.get("summary", "")

        if status == "Done":
            status_display = f"[green]{status}[/green]"
        elif status == "In Progress":
            status_display = f"[yellow]{status}[/yellow]"
        else:
            status_display = f"[white]{status}[/white]"

        table.add_row(key, issue_type, status_display, priority, summary)

    console.print(table)


@jira.command("show")
@click.argument("issue_key")
def jira_show(issue_key):
    """Vis detaljer for et Jira-issue."""
    from rich.panel import Panel

    try:
        client = _get_jira_client()
        issue = client.get_issue(issue_key)
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)
    except requests.RequestException as e:
        console.print(f"[red]Feil ved henting fra Jira:[/red] {e}")
        raise SystemExit(1)

    fields = issue.get("fields", {})
    key = issue.get("key", issue_key)
    summary = fields.get("summary", "")
    status = (fields.get("status") or {}).get("name", "")
    issue_type = (fields.get("issuetype") or {}).get("name", "")
    priority = (fields.get("priority") or {}).get("name", "")
    assignee_obj = fields.get("assignee") or {}
    assignee = assignee_obj.get("displayName", "Ingen")

    description_raw = fields.get("description")
    if isinstance(description_raw, dict):
        description = _adf_to_text(description_raw)
    else:
        description = description_raw or ""

    comments = (fields.get("comment") or {}).get("comments", [])
    last_comments = comments[-5:] if len(comments) > 5 else comments

    lines = [
        f"[bold]Status:[/bold] {status}",
        f"[bold]Type:[/bold] {issue_type}",
        f"[bold]Prioritet:[/bold] {priority}",
        f"[bold]Assignee:[/bold] {assignee}",
        "",
        f"[bold]Beskrivelse:[/bold]",
        description or "[dim](ingen beskrivelse)[/dim]",
    ]

    if last_comments:
        lines.append("")
        lines.append(f"[bold]Siste {len(last_comments)} kommentar(er):[/bold]")
        for comment in last_comments:
            author = (comment.get("author") or {}).get("displayName", "Ukjent")
            created = comment.get("created", "")[:10]
            body_raw = comment.get("body")
            if isinstance(body_raw, dict):
                body_text = _adf_to_text(body_raw)
            else:
                body_text = body_raw or ""
            lines.append(f"  [dim]{author} ({created}):[/dim] {body_text}")

    panel_content = "\n".join(lines)
    console.print(Panel(panel_content, title=f"[bold][{key}] {summary}[/bold]"))


@jira.command("create")
@click.option("--project", required=True, help="Jira project key")
@click.option("--summary", required=True, help="Issue summary")
@click.option("--type", "issue_type", default="Task", help="Issue type (default: Task)")
@click.option("--description", default="", help="Issue description")
def jira_create(project, summary, issue_type, description):
    """Opprett et nytt Jira-issue."""
    try:
        client = _get_jira_client()
        created = client.create_issue(
            project=project,
            summary=summary,
            issue_type=issue_type,
            description=description,
        )
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)
    except requests.RequestException as e:
        console.print(f"[red]Feil ved oppretting av issue:[/red] {e}")
        raise SystemExit(1)

    key = created.get("key", "")
    console.print(f"[green]Opprettet {key} — {summary}[/green]")


@jira.command("comment")
@click.argument("issue_key")
@click.argument("body")
def jira_comment(issue_key, body):
    """Legg til en kommentar på et Jira-issue."""
    try:
        client = _get_jira_client()
        client.add_comment(issue_key, body)
    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)
    except requests.RequestException as e:
        console.print(f"[red]Feil ved legging til kommentar:[/red] {e}")
        raise SystemExit(1)

    console.print(f"Kommentar lagt til på {issue_key}")


@jira.command("update")
@click.argument("issue_key")
@click.option("--status", default=None, help="Ny status (f.eks. 'In Progress')")
@click.option("--summary", default=None, help="Ny tittel")
@click.option("--assignee", default=None, help="Ny assignee (accountId eller navn)")
def jira_update(issue_key, status, summary, assignee):
    """Oppdater et Jira-issue."""
    try:
        client = _get_jira_client()

        if status is not None:
            client.transition_issue(issue_key, status)

        fields = {}
        if summary is not None:
            fields["summary"] = summary
        if assignee is not None:
            fields["assignee"] = {"name": assignee}

        if fields:
            client.update_issue(issue_key, fields)

    except FileNotFoundError as e:
        console.print(f"[red]Feil:[/red] {e}")
        raise SystemExit(1)
    except requests.RequestException as e:
        console.print(f"[red]Feil ved oppdatering av issue:[/red] {e}")
        raise SystemExit(1)

    console.print(f"Oppdatert {issue_key}")
