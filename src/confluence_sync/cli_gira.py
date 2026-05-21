import click
import requests
from rich.console import Console

from confluence_sync.auth import load_config
from confluence_sync.cli_common import auth_command

console = Console()


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


@click.group(invoke_without_command=True)
@click.version_option()
@click.pass_context
def main(ctx):
    """Gira — Jira fra terminalen."""
    if ctx.invoked_subcommand:
        from confluence_sync.banner import print_banner

        print_banner("gira")
    else:
        click.echo(ctx.get_help())


main.add_command(auth_command)


@main.command("list")
@click.option("--project", required=True, help="Jira project key")
@click.option("--jql", default=None, help="Custom JQL query")
@click.option("--limit", default=20, help="Max results")
def jira_list(project, jql, limit):
    """List issues i et Jira-prosjekt."""
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


@main.command("show")
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


@main.command("create")
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


@main.command("comment")
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


@main.command("update")
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
