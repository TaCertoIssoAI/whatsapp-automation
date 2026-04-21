#!/usr/bin/env python3
"""Conta a quantidade de documentos na coleção 'users-whatsapp' do Firebase.

Apenas leitura — nenhuma modificação é feita no banco de dados.
"""

import os
import sys
import warnings

# Use string-based module filtering BEFORE importing the modules 
# to catch the import-time warnings reliably.
warnings.filterwarnings("ignore", module="google.*")
warnings.filterwarnings("ignore", module="urllib3.*")

# Suppress urllib3 NotOpenSSLWarning specifically, just in case
try:
    import urllib3
    warnings.filterwarnings("ignore", category=urllib3.exceptions.NotOpenSSLWarning)
except ImportError:
    pass

import firebase_admin
from firebase_admin import credentials, firestore

_CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "firebase-credentials.json")
_DATABASE_ID = "tacertoissoai"
_COLLECTION = "users-whatsapp"


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

    docs = db.collection(_COLLECTION).stream()
    count = sum(1 for _ in docs)

    print(f"Total de documentos em '{_COLLECTION}': {count}")


if __name__ == "__main__":
    main()
