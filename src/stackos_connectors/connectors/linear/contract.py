"""Fixed provider action routes; callers cannot select arbitrary endpoints."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LinearActionSpec:
    document: str
    root: str
    scope: str
    output_type: str | None = None

    @property
    def write(self) -> bool:
        return self.scope == "write"


LINEAR_ACTION_SPECS: dict[str, LinearActionSpec] = {
    "viewer.get": LinearActionSpec("graphql/viewer/get.graphql", "viewer", "read", "user"),
    "teams.list": LinearActionSpec("graphql/teams/list.graphql", "teams", "read", "team"),
    "teams.get": LinearActionSpec("graphql/teams/get.graphql", "team", "read", "team"),
    "users.list": LinearActionSpec("graphql/users/list.graphql", "users", "read", "user"),
    "users.get": LinearActionSpec("graphql/users/get.graphql", "user", "read", "user"),
    "workflow_states.list": LinearActionSpec(
        "graphql/workflow-states/list.graphql", "workflowStates", "read", "workflow-state"
    ),
    "workflow_states.get": LinearActionSpec(
        "graphql/workflow-states/get.graphql", "workflowState", "read", "workflow-state"
    ),
    "projects.list": LinearActionSpec(
        "graphql/projects/list.graphql", "projects", "read", "project"
    ),
    "projects.get": LinearActionSpec("graphql/projects/get.graphql", "project", "read", "project"),
    "cycles.list": LinearActionSpec("graphql/cycles/list.graphql", "cycles", "read", "cycle"),
    "cycles.get": LinearActionSpec("graphql/cycles/get.graphql", "cycle", "read", "cycle"),
    "issue_labels.list": LinearActionSpec(
        "graphql/issue-labels/list.graphql", "issueLabels", "read", "issue-label"
    ),
    "issue_labels.get": LinearActionSpec(
        "graphql/issue-labels/get.graphql", "issueLabel", "read", "issue-label"
    ),
    "issues.list": LinearActionSpec("graphql/issues/list.graphql", "issues", "read", "issue"),
    "issues.get": LinearActionSpec("graphql/issues/get.graphql", "issue", "read", "issue"),
    "issues.search": LinearActionSpec(
        "graphql/issues/search.graphql", "searchIssues", "read", "issue"
    ),
    "comments.list": LinearActionSpec(
        "graphql/comments/list.graphql", "comments", "read", "comment"
    ),
    "comments.get": LinearActionSpec("graphql/comments/get.graphql", "comment", "read", "comment"),
    "issue_relations.list": LinearActionSpec(
        "graphql/issue-relations/list.graphql", "issue", "read", "issue"
    ),
    "issue_relations.get": LinearActionSpec(
        "graphql/issue-relations/get.graphql", "issueRelation", "read", "issue-relation"
    ),
    "issues.create": LinearActionSpec(
        "graphql/issues/create.graphql", "issueCreate", "write", "issue"
    ),
    "issues.update": LinearActionSpec(
        "graphql/issues/update.graphql", "issueUpdate", "write", "issue"
    ),
    "issues.archive": LinearActionSpec(
        "graphql/issues/archive.graphql", "issueArchive", "write", "issue"
    ),
    "issues.unarchive": LinearActionSpec(
        "graphql/issues/unarchive.graphql", "issueUnarchive", "write", "issue"
    ),
    "issues.labels.add": LinearActionSpec(
        "graphql/issues/add-label.graphql", "issueAddLabel", "write", "issue"
    ),
    "issues.labels.remove": LinearActionSpec(
        "graphql/issues/remove-label.graphql", "issueRemoveLabel", "write", "issue"
    ),
    "comments.create": LinearActionSpec(
        "graphql/comments/create.graphql", "commentCreate", "write", "comment"
    ),
    "comments.update": LinearActionSpec(
        "graphql/comments/update.graphql", "commentUpdate", "write", "comment"
    ),
    "comments.resolve": LinearActionSpec(
        "graphql/comments/resolve.graphql", "commentResolve", "write", "comment"
    ),
    "comments.unresolve": LinearActionSpec(
        "graphql/comments/unresolve.graphql", "commentUnresolve", "write", "comment"
    ),
    "comments.delete": LinearActionSpec(
        "graphql/comments/delete.graphql", "commentDelete", "write"
    ),
    "issue_relations.create": LinearActionSpec(
        "graphql/issue-relations/create.graphql", "issueRelationCreate", "write", "issue-relation"
    ),
    "issue_relations.update": LinearActionSpec(
        "graphql/issue-relations/update.graphql", "issueRelationUpdate", "write", "issue-relation"
    ),
    "issue_relations.delete": LinearActionSpec(
        "graphql/issue-relations/delete.graphql", "issueRelationDelete", "write"
    ),
}
