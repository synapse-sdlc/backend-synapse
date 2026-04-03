"""add metrics confidence columns

Revision ID: 9602252ae758
Revises: 66cf4c2a9e52
Create Date: 2026-04-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '9602252ae758'
down_revision: Union[str, None] = '66cf4c2a9e52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Artifact: confidence score + version linking
    op.add_column('artifacts', sa.Column('confidence_score', sa.Integer(), nullable=True))
    op.add_column('artifacts', sa.Column('previous_version_id', sa.String(12), sa.ForeignKey('artifacts.id'), nullable=True))

    # Feature: cost/time metrics
    op.add_column('features', sa.Column('total_turns', sa.Integer(), server_default='0', nullable=False))
    op.add_column('features', sa.Column('total_duration_ms', sa.Integer(), server_default='0', nullable=False))
    op.add_column('features', sa.Column('estimated_hours_saved', sa.Float(), server_default='0.0', nullable=False))

    # Project: custom skills storage
    op.add_column('projects', sa.Column('custom_skills', JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('projects', 'custom_skills')
    op.drop_column('features', 'estimated_hours_saved')
    op.drop_column('features', 'total_duration_ms')
    op.drop_column('features', 'total_turns')
    op.drop_column('artifacts', 'previous_version_id')
    op.drop_column('artifacts', 'confidence_score')
