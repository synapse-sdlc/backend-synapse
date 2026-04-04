"""add branch_name and jira_issue_key to pull_request_links

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('pull_request_links', sa.Column(
        'branch_name', sa.String(255), nullable=True))
    op.add_column('pull_request_links', sa.Column(
        'jira_issue_key', sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column('pull_request_links', 'jira_issue_key')
    op.drop_column('pull_request_links', 'branch_name')
