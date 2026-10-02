"""Diagnóstico rápido de la conexión a Azure AI Content Safety.

Ejecuta esto en la MISMA carpeta donde está tu .env real:

    python diagnose.py

No modifica nada. Solo lee las variables SAFETY_CONTENT_ENDPOINT /
SAFETY_CONTENT_KEY (y de paso SAFETY_ENDPOINT / SAFETY_API_KEY) del .env de
esa carpeta y hace una llamada real de prueba a Content Safety, imprimiendo
el error exacto si algo falla. No imprime tus claves completas.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_dotenv(path):
    if not path.is_file():
        print(f"⚠️  No encontré {path} — este script debe correr en la carpeta donde está tu .env real.")
        return
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


def mask(v):
    if not v:
        return '(vacío)'
    return v[:6] + '…' + v[-4:] if len(v) > 12 else '(muy corto, revisa que no esté cortado)'


def main():
    load_dotenv(ROOT / '.env')

    endpoint = os.environ.get('SAFETY_ENDPOINT', '').strip()
    api_key = os.environ.get('SAFETY_API_KEY', '').strip()
    models = os.environ.get('SAFETY_MODELS', '').strip()
    content_endpoint = os.environ.get('SAFETY_CONTENT_ENDPOINT', '').strip().rstrip('/')
    content_key = os.environ.get('SAFETY_CONTENT_KEY', '').strip()

    print("=" * 64)
    print("Variables leídas del .env (valores enmascarados)")
    print("=" * 64)
    print(f"SAFETY_ENDPOINT          = {endpoint or '(vacío)'}")
    print(f"SAFETY_API_KEY           = {mask(api_key)}")
    print(f"SAFETY_MODELS            = {models or '(vacío)'}")
    print(f"SAFETY_CONTENT_ENDPOINT  = {content_endpoint or '(vacío)'}")
    print(f"SAFETY_CONTENT_KEY       = {mask(content_key)}")
    print()

    if not content_endpoint or not content_key:
        print("❌ Falta SAFETY_CONTENT_ENDPOINT y/o SAFETY_CONTENT_KEY en el .env de esta carpeta.")
        print("   Ese es el motivo del error 'No se completaron todos los controles de entrada'.")
        sys.exit(1)

    def try_analyze(label, payload_extra):
        print("=" * 64)
        print(f"Probando Content Safety · text:analyze ({label})")
        print("=" * 64)
        url = content_endpoint + '/contentsafety/text:analyze?api-version=2024-09-01'
        body = {
            'text': 'Hola, esto es una prueba de diagnóstico.',
            'categories': ['Hate', 'SelfHarm', 'Sexual', 'Violence'],
            'outputType': 'EightSeverityLevels',
        }
        body.update(payload_extra)
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json', 'Ocp-Apim-Subscription-Key': content_key},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.load(resp)
            print("✅ Respondió OK:")
            print(json.dumps(result.get('categoriesAnalysis', result), indent=2, ensure_ascii=False))
            return True
        except urllib.error.HTTPError as e:
            detail = e.read(4096).decode('utf-8', errors='replace')
            print(f"❌ HTTPError {e.code} {e.reason}")
            print(f"   URL llamada: {url}")
            print(f"   Cuerpo de la respuesta de Azure:\n   {detail}")
        except urllib.error.URLError as e:
            print(f"❌ URLError (problema de red/DNS/firewall): {e.reason}")
            print(f"   URL llamada: {url}")
        except Exception as e:
            print(f"❌ Error inesperado: {type(e).__name__}: {e}")
        return False
        print()

    ok_with_blocklist = try_analyze(
        'CON blocklistNames=[blocklist1], como usa app.py',
        {'blocklistNames': ['blocklist1'], 'haltOnBlocklistHit': False},
    )
    print()
    ok_without_blocklist = try_analyze('SIN blocklistNames', {})
    print()
    if not ok_with_blocklist and ok_without_blocklist:
        print("👉 CONFIRMADO: el problema es el blocklist 'blocklist1'. La llamada")
        print("   funciona sin él, así que ese blocklist no existe (o no se llama así)")
        print("   en este recurso de Content Safety. Hay que recrearlo o quitar la")
        print("   referencia en app.py.")
        print()

    print()
    print("=" * 64)
    print("Probando Content Safety · text:shieldPrompt (Prompt Shield)")
    print("=" * 64)
    url2 = content_endpoint + '/contentsafety/text:shieldPrompt?api-version=2024-09-01'
    req2 = urllib.request.Request(
        url2, data=json.dumps({'userPrompt': 'Hola, esto es una prueba.', 'documents': []}).encode(),
        headers={'Content-Type': 'application/json', 'Ocp-Apim-Subscription-Key': content_key},
    )
    try:
        with urllib.request.urlopen(req2, timeout=30) as resp:
            result = json.load(resp)
        print("✅ shieldPrompt respondió OK:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except urllib.error.HTTPError as e:
        detail = e.read(4096).decode('utf-8', errors='replace')
        print(f"❌ HTTPError {e.code} {e.reason}")
        print(f"   URL llamada: {url2}")
        print(f"   Cuerpo de la respuesta de Azure:\n   {detail}")
    except urllib.error.URLError as e:
        print(f"❌ URLError (problema de red/DNS/firewall): {e.reason}")
        print(f"   URL llamada: {url2}")
    except Exception as e:
        print(f"❌ Error inesperado: {type(e).__name__}: {e}")


if __name__ == '__main__':
    main()
