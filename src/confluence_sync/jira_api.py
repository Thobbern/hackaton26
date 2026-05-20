import requests


class JiraClient:
    def __init__(self, instance_url: str, email: str, api_token: str):
        self.base_url = f"https://{instance_url}/rest/api/3"
        self.session = requests.Session()
        self.session.auth = (email, api_token)
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def search_issues(self, jql: str, max_results: int = 50) -> list[dict]:
        resp = self.session.post(f"{self.base_url}/search", json={
            "jql": jql,
            "maxResults": max_results,
            "fields": ["summary", "status", "assignee", "issuetype", "priority", "created", "updated", "comment"],
        })
        resp.raise_for_status()
        return resp.json()["issues"]

    def get_issue(self, issue_key: str) -> dict:
        resp = self.session.get(
            f"{self.base_url}/issue/{issue_key}",
            params={"fields": "summary,status,assignee,issuetype,priority,description,comment,created,updated"},
        )
        resp.raise_for_status()
        return resp.json()

    def create_issue(self, project_key: str, summary: str, issue_type: str = "Task", description: str = "") -> dict:
        fields: dict = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }
        if description:
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}],
            }
        resp = self.session.post(f"{self.base_url}/issue", json={"fields": fields})
        resp.raise_for_status()
        return resp.json()

    def add_comment(self, issue_key: str, body: str) -> dict:
        resp = self.session.post(f"{self.base_url}/issue/{issue_key}/comment", json={
            "body": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": body}]}],
            }
        })
        resp.raise_for_status()
        return resp.json()

    def update_issue(self, issue_key: str, fields: dict) -> None:
        resp = self.session.put(f"{self.base_url}/issue/{issue_key}", json={"fields": fields})
        resp.raise_for_status()

    def transition_issue(self, issue_key: str, transition_name: str) -> None:
        resp = self.session.get(f"{self.base_url}/issue/{issue_key}/transitions")
        resp.raise_for_status()
        transitions = resp.json()["transitions"]
        match = next(
            (t for t in transitions if t["name"].lower() == transition_name.lower()),
            None,
        )
        if match is None:
            available = [t["name"] for t in transitions]
            raise ValueError(f"Transition '{transition_name}' not found. Available: {available}")
        resp = self.session.post(f"{self.base_url}/issue/{issue_key}/transitions", json={
            "transition": {"id": match["id"]}
        })
        resp.raise_for_status()

    def get_projects(self) -> list[dict]:
        resp = self.session.get(f"{self.base_url}/project/search", params={"maxResults": 50})
        resp.raise_for_status()
        return resp.json()["values"]
