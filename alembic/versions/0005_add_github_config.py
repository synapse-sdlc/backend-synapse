"""add github_configs table

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'github_configs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('project_id', UUID(as_uuid=True), sa.ForeignKey(
            'projects.id'), nullable=False, unique=True),
        sa.Column('github_token_encrypted', sa.Text(), nullable=True),
        sa.Column('webhook_secret', sa.String(64), nullable=True),
        sa.Column('signing_secret', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index(
        'ix_github_configs_project_id',
        'github_configs',
        ['project_id'],
        unique=True,
    )
    op.create_index(
        'ix_github_configs_webhook_secret',
        'github_configs',
        ['webhook_secret'],
    )


def downgrade() -> None:
    op.drop_index('ix_github_configs_webhook_secret',
                  table_name='github_configs')
    op.drop_index('ix_github_configs_project_id', table_name='github_configs')
    op.drop_table('github_configs')
