"""Firebase Admin and Firestore initialization helpers.

This module keeps SDK initialization in one place so the rest of the
application can depend on a single, stable entry point for Firestore access.
`get_firestore()` is memoized to avoid rebuilding the Firestore client on every
call and to prevent repeated Firebase initialization work during the process
lifetime.
"""

from functools import lru_cache
import logging

import firebase_admin
from firebase_admin import credentials, firestore as admin_firestore
from google.cloud import firestore

from app.core.config import settings

logger = logging.getLogger(__name__)


def init_firebase() -> firebase_admin.App:
    """Initialize Firebase Admin once and return the active app instance."""
    if firebase_admin._apps:
        return firebase_admin.get_app()

    try:
        credential = credentials.Certificate(settings.GOOGLE_APPLICATION_CREDENTIALS)
        return firebase_admin.initialize_app(
            credential=credential,
            options={"projectId": settings.FIREBASE_PROJECT_ID},
        )
    except Exception:
        logger.exception("Failed to initialize Firebase Admin SDK.")
        raise


@lru_cache()
def get_firestore() -> firestore.Client:
    """Return a memoized Firestore client for the configured Firebase project.

    Memoization ensures the client is created only once per process, which keeps
    SDK startup centralized and avoids duplicate initialization paths across the
    application.
    """
    app = init_firebase()
    return admin_firestore.client(app=app)
