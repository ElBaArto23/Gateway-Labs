"""Crea (o actualiza) el blocklist personalizado de Azure AI Content Safety
que usan tanto la app web (SAFETY_BLOCKLIST_NAME, por defecto "blocklist1")
como el bloque <llm-content-safety> de policy.xml.

Por qué existe este script: el blocklist NO es un recurso de infraestructura
(no se crea con Bicep/ARM) -- vive en el plano de datos de Content Safety, así
que cada vez que se redespliega el lab hay que volver a crearlo. Si no existe
y algo lo referencia (la app, o la policy de APIM), Azure responde un 500
genérico ("InternalError: Service unavailable") que parece una caída del
servicio pero no lo es -- es justo el problema que diagnosticamos hoy.

Uso:
    python provision_blocklist.py

Lee SAFETY_CONTENT_ENDPOINT / SAFETY_CONTENT_KEY / SAFETY_BLOCKLIST_NAME del
.env de esta misma carpeta. Es idempotente: correrlo varias veces no duplica
nada ni rompe lo que ya exista.

Para agregar tus propios términos de empresa, edita ITEMS_A_AGREGAR más abajo.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API_VERSION = "2024-09-01"

# ---------------------------------------------------------------------------
# Edita esta lista con los términos/frases reales de tu empresa. El marcador
# LAB-CS-DEMO-BLOCK es inocuo a propósito -- sirve para probar que el
# blocklist funciona sin necesidad de escribir contenido real bloqueable.
# ---------------------------------------------------------------------------
ITEMS_A_AGREGAR = [
    {"text": "LAB-CS-DEMO-BLOCK", "description": "Marcador de prueba del laboratorio (inocuo)"},
    # {"text": "tu-termino-aqui", "description": "por qué se bloquea"},
]


def load_dotenv(path):
    if not path.is_file():
        print(f"⚠️  No encontré {path} — corre este script en la carpeta de tu .env real.")
        sys.exit(1)
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            os.environ[key] = value


def call(method, url, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={'Content-Type': 'application/json', 'Ocp-Apim-Subscription-Key': key},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def main():
    load_dotenv(ROOT / '.env')
    endpoint = os.environ.get('SAFETY_CONTENT_ENDPOINT', '').strip().rstrip('/')
    key = os.environ.get('SAFETY_CONTENT_KEY', '').strip()
    blocklist_name = os.environ.get('SAFETY_BLOCKLIST_NAME', 'blocklist1').strip() or 'blocklist1'

    if not endpoint or not key:
        print("❌ Falta SAFETY_CONTENT_ENDPOINT y/o SAFETY_CONTENT_KEY en el .env.")
        sys.exit(1)

    print("=" * 64)
    print(f"1/3 · Creando (o confirmando) el blocklist '{blocklist_name}'")
    print("=" * 64)
    url_blocklist = f"{endpoint}/contentsafety/text/blocklists/{blocklist_name}?api-version={API_VERSION}"
    try:
        result = call('PATCH', url_blocklist, key, {
            'description': 'Blocklist empresarial del laboratorio Content Safety (Controles Empresariales).',
        })
        print(f"✅ Blocklist '{blocklist_name}' listo: {json.dumps(result, ensure_ascii=False)}")
    except urllib.error.HTTPError as e:
        detail = e.read(4096).decode('utf-8', errors='replace')
        print(f"❌ HTTPError {e.code} {e.reason} creando el blocklist:\n   {detail}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"❌ URLError (red/DNS/firewall) creando el blocklist: {e.reason}")
        sys.exit(1)

    print()
    print("=" * 64)
    print(f"2/3 · Agregando {len(ITEMS_A_AGREGAR)} término(s) al blocklist")
    print("=" * 64)
    url_items = f"{endpoint}/contentsafety/text/blocklists/{blocklist_name}:addOrUpdateBlocklistItems?api-version={API_VERSION}"
    try:
        result = call('POST', url_items, key, {'blocklistItems': ITEMS_A_AGREGAR})
        added = result.get('blocklistItems', [])
        for item in added:
            print(f"   ✅ agregado: {item.get('text')!r} (id={item.get('blocklistItemId')})")
        if not added:
            print(f"   (respuesta: {json.dumps(result, ensure_ascii=False)})")
    except urllib.error.HTTPError as e:
        detail = e.read(4096).decode('utf-8', errors='replace')
        print(f"❌ HTTPError {e.code} {e.reason} agregando términos:\n   {detail}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"❌ URLError (red/DNS/firewall) agregando términos: {e.reason}")
        sys.exit(1)

    print()
    print("=" * 64)
    print("3/3 · Verificando el contenido actual del blocklist")
    print("=" * 64)
    url_list = f"{endpoint}/contentsafety/text/blocklists/{blocklist_name}/blocklistItems?api-version={API_VERSION}"
    try:
        result = call('GET', url_list, key)
        for item in result.get('value', []):
            print(f"   - {item.get('text')!r}")
    except urllib.error.HTTPError as e:
        detail = e.read(4096).decode('utf-8', errors='replace')
        print(f"❌ HTTPError {e.code} {e.reason} listando términos:\n   {detail}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"❌ URLError (red/DNS/firewall) listando términos: {e.reason}")
        sys.exit(1)

    print()
    print("✅ Listo. El blocklist ya existe y app.py / policy.xml pueden usarlo.")
    print("   Reintenta el chat -- ya no debería fallar por esto.")


if __name__ == '__main__':
    main()
