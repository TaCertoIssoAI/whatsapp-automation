#!/usr/bin/env python3
"""Conta usuários que enviaram pelo menos uma mensagem hoje.

Apenas leitura — nenhuma modificação é feita no banco de dados.
Filtra documentos onde 'lastInteractionDate' == data de hoje (UTC, YYYY-MM-DD).
"""

import os
import sys
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter

_CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "firebase-credentials.json")
_DATABASE_ID = "tacertoissoai"
_COLLECTION = "users-whatsapp"


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def main() -> None:
    if not os.path.exists(_CREDENTIALS_PATH):
        print(f"[ERRO] Arquivo de credenciais não encontrado: {_CREDENTIALS_PATH}")
        sys.exit(1)

    try:
        app = firebase_admin.get_app()
    except ValueError:
        cred = credentials.Certificate(_CREDENTIALS_PATH)
        app = firebase_admin.initialize_app(cred)

    db = firestore.client(database_id=_DATABASE_ID)

    today = today_utc()
    docs = (
        db.collection(_COLLECTION)
        .where(filter=FieldFilter("lastInteractionDate", "==", today))
        .stream()
    )
    count = sum(1 for _ in docs)

    print(f"Usuários que enviaram mensagem hoje ({today}): {count}")


if __name__ == "__main__":
    main()
