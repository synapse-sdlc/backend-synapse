"""add deployment_status to pull_request_links

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('pull_request_links', sa.Column('deployment_status', JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column('pull_request_links', 'deployment_status')
