"""add webhook secret and issue_key index

Revision ID: b3f1a2c4d5e6
Revises: 9602252ae758
Create Date: 2026-04-03 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3f1a2c4d5e6'
down_revision: Union[str, None] = '9602252ae758'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('jira_configs', sa.Column('webhook_secret', sa.String(64), nullable=True))
    op.add_column('jira_configs', sa.Column('jira_webhook_secret', sa.Text(), nullable=True))
    op.create_index('ix_jira_issue_links_issue_key', 'jira_issue_links', ['issue_key'])


def downgrade() -> None:
    op.drop_index('ix_jira_issue_links_issue_key', table_name='jira_issue_links')
    op.drop_column('jira_configs', 'jira_webhook_secret')
    op.drop_column('jira_configs', 'webhook_secret')
