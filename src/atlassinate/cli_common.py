import click
from rich.console import Console

from atlassinate.auth import save_config, validate_credentials

console = Console()


@click.command("auth")
def auth_command():
    """Koble til Atlassian Cloud med API-token."""
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
