"""initial schema — all tables, deploy-ready

Revision ID: 0001
Revises:
Create Date: 2026-04-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- orgs ---
    op.create_table('orgs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )

    # --- projects ---
    op.create_table('projects',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('org_id', UUID(as_uuid=True), sa.ForeignKey('orgs.id'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('github_url', sa.Text, nullable=True),
        sa.Column('analysis_status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('github_token_encrypted', sa.Text, nullable=True),
        sa.Column('s3_repo_key', sa.Text, nullable=True),
        sa.Column('codebase_context', sa.Text, nullable=True),
        sa.Column('config', JSONB, nullable=True),
        sa.Column('uploaded_architecture_id', sa.String(12), nullable=True),
        sa.Column('custom_skills', JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )

    # --- users ---
    op.create_table('users',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('org_id', UUID(as_uuid=True), sa.ForeignKey('orgs.id'), nullable=False),
        sa.Column('email', sa.String(255), nullable=False, unique=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('password_hash', sa.Text, nullable=False),
        sa.Column('role', sa.String(20), nullable=False, server_default='admin'),
        sa.Column('auth_provider', sa.String(20), nullable=True),
        sa.Column('auth_provider_id', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )

    # --- features ---
    op.create_table('features',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('project_id', UUID(as_uuid=True), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('description', sa.Text, nullable=False),
        sa.Column('phase', sa.String(20), nullable=False, server_default='gathering'),
        sa.Column('spec_artifact_id', sa.String(12), nullable=True),
        sa.Column('plan_artifact_id', sa.String(12), nullable=True),
        sa.Column('tests_artifact_id', sa.String(12), nullable=True),
        sa.Column('jira_epic_key', sa.String(30), nullable=True),
        sa.Column('agent_task_id', sa.String(255), nullable=True),
        sa.Column('total_turns', sa.Integer, nullable=False, server_default='0'),
        sa.Column('total_duration_ms', sa.Integer, nullable=False, server_default='0'),
        sa.Column('estimated_hours_saved', sa.Float, nullable=False, server_default='0.0'),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )

    # --- artifacts ---
    op.create_table('artifacts',
        sa.Column('id', sa.String(12), primary_key=True),
        sa.Column('type', sa.String(20), nullable=False),
        sa.Column('name', sa.Text, nullable=False),
        sa.Column('content', JSONB, nullable=False),
        sa.Column('content_md', sa.Text, nullable=True),
        sa.Column('parent_id', sa.String(12), sa.ForeignKey('artifacts.id'), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='draft'),
        sa.Column('version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('feature_id', UUID(as_uuid=True), sa.ForeignKey('features.id'), nullable=True),
        sa.Column('project_id', UUID(as_uuid=True), sa.ForeignKey('projects.id'), nullable=True),
        sa.Column('repo_id', UUID(as_uuid=True), nullable=True),
        sa.Column('confidence_score', sa.Integer, nullable=True),
        sa.Column('previous_version_id', sa.String(12), sa.ForeignKey('artifacts.id'), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )

    # --- messages ---
    op.create_table('messages',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('feature_id', UUID(as_uuid=True), sa.ForeignKey('features.id'), nullable=False),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('tool_name', sa.String(100), nullable=True),
        sa.Column('tool_calls', JSONB, nullable=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('user_name', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )

    # --- repositories ---
    op.create_table('repositories',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('project_id', UUID(as_uuid=True), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('repo_type', sa.String(30), nullable=True),
        sa.Column('github_url', sa.Text, nullable=False),
        sa.Column('github_token_encrypted', sa.Text, nullable=True),
        sa.Column('s3_repo_key', sa.Text, nullable=True),
        sa.Column('analysis_status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('codebase_context', sa.Text, nullable=True),
        sa.Column('config', JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )

    # --- jira_configs ---
    op.create_table('jira_configs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('project_id', UUID(as_uuid=True), sa.ForeignKey('projects.id'), unique=True, nullable=False),
        sa.Column('site_url', sa.Text, nullable=False),
        sa.Column('user_email', sa.String(255), nullable=False),
        sa.Column('api_token_encrypted', sa.Text, nullable=False),
        sa.Column('default_project_key', sa.String(20), nullable=False),
        sa.Column('webhook_secret', sa.String(64), nullable=True),
        sa.Column('jira_webhook_secret', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )

    # --- jira_issue_links ---
    op.create_table('jira_issue_links',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('feature_id', UUID(as_uuid=True), sa.ForeignKey('features.id'), nullable=False),
        sa.Column('issue_key', sa.String(30), nullable=False),
        sa.Column('issue_type', sa.String(20), nullable=False),
        sa.Column('issue_url', sa.Text, nullable=False),
        sa.Column('summary', sa.Text, nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='To Do'),
        sa.Column('parent_issue_key', sa.String(30), nullable=True),
        sa.Column('source_artifact_id', sa.String(12), nullable=True),
        sa.Column('source_item_id', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('status_synced_at', sa.DateTime, nullable=True),
    )
    op.create_index('ix_jira_issue_links_issue_key', 'jira_issue_links', ['issue_key'])

    # --- pull_request_links ---
    op.create_table('pull_request_links',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('feature_id', UUID(as_uuid=True), sa.ForeignKey('features.id'), nullable=False),
        sa.Column('repo_full_name', sa.String(255), nullable=False),
        sa.Column('pr_number', sa.Integer, nullable=False),
        sa.Column('pr_url', sa.Text, nullable=False),
        sa.Column('title', sa.Text, nullable=False),
        sa.Column('state', sa.String(20), nullable=False, server_default='open'),
        sa.Column('merged_at', sa.DateTime, nullable=True),
        sa.Column('diff_summary', sa.Text, nullable=True),
        sa.Column('files_changed', JSONB, nullable=True),
        sa.Column('commit_messages', JSONB, nullable=True),
        sa.Column('kb_updated', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('synced_at', sa.DateTime, nullable=True),
    )

    # --- knowledge_entries ---
    op.create_table('knowledge_entries',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('project_id', UUID(as_uuid=True), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('feature_id', UUID(as_uuid=True), sa.ForeignKey('features.id'), nullable=True),
        sa.Column('entry_type', sa.String(30), nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('metadata', JSONB, nullable=True),
        sa.Column('tags', ARRAY(sa.Text), nullable=True),
        sa.Column('source_artifact_id', sa.String(12), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )

    # --- api_contracts ---
    op.create_table('api_contracts',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('project_id', UUID(as_uuid=True), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('method', sa.String(10), nullable=False),
        sa.Column('path', sa.String(500), nullable=False),
        sa.Column('provider_repo_id', UUID(as_uuid=True), nullable=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('request_schema', JSONB, nullable=True),
        sa.Column('response_schema', JSONB, nullable=True),
        sa.Column('auth_required', sa.Boolean, nullable=True),
        sa.Column('consumers', JSONB, nullable=True),
        sa.Column('extracted_from', sa.String(20), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )

    # --- shared_models ---
    op.create_table('shared_models',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('project_id', UUID(as_uuid=True), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('canonical_repo_id', UUID(as_uuid=True), nullable=True),
        sa.Column('schema', JSONB, nullable=True),
        sa.Column('usages', JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table('shared_models')
    op.drop_table('api_contracts')
    op.drop_table('knowledge_entries')
    op.drop_table('pull_request_links')
    op.drop_index('ix_jira_issue_links_issue_key', table_name='jira_issue_links')
    op.drop_table('jira_issue_links')
    op.drop_table('jira_configs')
    op.drop_table('repositories')
    op.drop_table('messages')
    op.drop_table('artifacts')
    op.drop_table('features')
    op.drop_table('users')
    op.drop_table('projects')
    op.drop_table('orgs')
