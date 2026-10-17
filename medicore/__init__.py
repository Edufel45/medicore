"""
MediCore — project package.

Import the Celery app so that `@shared_task` decorators work
everywhere and the worker starts with `celery -A medicore worker`.
"""

from .celery import app as celery_app

__all__ = ("celery_app",)