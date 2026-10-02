"""Chat WSGI para Azure App Service y el gateway del laboratorio Content Safety.

Punto de control único: esta app NO llama a Azure AI Content Safety por su
cuenta. Todo el prompt (y, gracias a enforce-on-completions="true", también
la respuesta del modelo) pasa por Azure API Management, que ejecuta la
politica llm-content-safety (ver policy.xml) contra Content Safety. La
decision de bloquear o permitir la toma APIM, nunca esta app: la app solo
llama al gateway y, si APIM responde 403, lee el JSON estructurado que arma
policy.xml (causa_bloqueo, severidad, blocklist, Prompt Shield) para mostrar
la evidencia. Si APIM responde 200, el contenido ya paso los dos controles
(entrada y salida) dentro de APIM.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = '2026-09-23-gateway-routing-severity'
SYSTEM = 'You are a helpful AI assistant. Answer clearly and safely. Respond in the user language.'

# Palabras/frases que sugieren una pregunta que pide razonamiento, análisis o
# una respuesta elaborada -- esas van al modelo grande aunque el texto sea
# corto (p.ej. "compara X con Y"). Esto es solo enrutamiento de modelo, no
# tiene nada que ver con seguridad de contenido.
_COMPLEX_KEYWORDS = (
    'explica', 'explicar', 'explícame', 'analiza', 'analizar', 'compara',
    'comparar', 'resume', 'resumir', 'argumenta', 'demuestra', 'demostrar',
    'razona', 'razonamiento', 'código', 'codigo', 'programa', 'programar',
    'calcula', 'calcular', 'ensayo', 'detalla', 'detallado', 'profundiza',
    'investiga', 'estrategia', 'por qué', 'porque', 'causas',
    'consecuencias', 'ventajas', 'desventajas', 'diferencia entre',
    'paso a paso', 'plan de', 'resumen de',
)
_AUTO_MODEL_VALUE = '__auto__'

# Patrones de arranque típicos de una negativa del modelo, en español e
# inglés. Esto es una heurística de texto -- NO es el campo estructurado
# `message.refusal` de la API (que muy pocos modelos/versiones entregan). Se
# usa solo cuando ese campo no vino, para no dejar una negativa en texto plano
# clasificada silenciosamente como 'ALLOWED' sin ninguna señal.
#
# Antes esto era una lista cerrada de frases exactas ("no puedo generar",
# "no puedo ayudar con eso", etc.) y por eso no detectó "No puedo DAR
# instrucciones para matar..." -- el verbo "dar" no estaba en la lista. En
# vez de perseguir cada verbo uno por uno, esto usa un patrón: "no puedo/no
# puedes/no está permitido" + CUALQUIER verbo, o el equivalente en inglés
# ("I can't/cannot/won't" + cualquier verbo). Sigue siendo una heurística de
# texto, no un análisis semántico -- puede fallar en ambos sentidos: no
# detecta negativas con otra redacción (p.ej. "Prefiero no responder a
# eso"), y puede marcar como negativa una respuesta legítima que arranca
# parecido (p.ej. "No puedo confirmar esa cifra con certeza, pero..."). Por
# eso se etiqueta explícitamente en la evidencia como detección por texto,
# distinta de la señal estructurada.
_REFUSAL_RE = re.compile(
    r'^(lo (siento|lamento)[,.]?\s*)?(pero\s+)?no (puedo|podemos|está permitido que|te puedo|le puedo)\s+\w+'
    r'|^como (asistente|ia|modelo)[^.]{0,60}?no (puedo|puede)\s+\w+'
    r"|^(i'?m|i am) (sorry|afraid)\b[^.]{0,60}?\b(can'?t|cannot|won'?t)\b"
    r"|^sorry,?\s*(but\s+)?i\s+(can'?t|cannot|won'?t)\s+\w+"
    r"|^i\s+(can'?t|cannot|won'?t)\s+\w+",
)


def _looks_like_refusal(text):
    """Heurística: ¿el texto arranca como una negativa típica del modelo?

    Solo mira el inicio del texto (primeros ~160 caracteres, normalizado) para
    reducir falsos positivos por menciones de este patrón más adelante en una
    respuesta normal.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    head = text.strip().lower()[:160]
    return _REFUSAL_RE.search(head) is not None

# Causas de bloqueo que policy.xml puede devolver en causa_bloqueo (ver
# on-error de policy.xml). categoria_entrada/blocklist/prompt_shield se
# deducen de una re-consulta al texto ORIGINAL DEL PROMPT, así que las tres
# corresponden con certeza a la etapa de entrada.
_PROMPT_STAGE_CAUSES = ('categoria_entrada', 'blocklist', 'prompt_shield')
_RESPONSE_STAGE_CAUSE = 'salida_del_modelo_probable'
# Causa que arma el OUTBOUND de policy.xml (no el on-error): la entrada tenía
# algún nivel de riesgo, el gateway la envió al modelo pequeño y el modelo se
# negó a responder. El modelo sí se ejecutó, así que la etapa es 'response'.
_GATEWAY_REFUSAL_CAUSE = 'negativa_del_modelo_con_riesgo'

# Niveles de riesgo que policy.xml pone en el header x-cs-nivel (y en
# nivel_riesgo del 403), con el texto que se muestra en la evidencia.
_RISK_LEVEL_TEXT = {
    'limpio': 'Entrada sin riesgo (severidad 0 en todas las categorías): el gateway la envió al modelo grande.',
    'riesgo_leve': 'Entrada con riesgo leve (severidad por debajo del umbral de bloqueo): el gateway la envió al modelo pequeño.',
    'bloqueo_esperado': 'La entrada alcanza el umbral de bloqueo o coincide con blocklist1.',
    'sin_analisis': 'El gateway no pudo medir la severidad de la entrada; por precaución la envió al modelo pequeño.',
}


def choose_model_auto(prompt_text, models):
    """Elige entre los modelos configurados según la complejidad del texto.

    Heurística simple para la demo: el primer modelo de SAFETY_MODELS se
    trata como el modelo grande (p.ej. gpt-5.6-luna) y el último como el
    modelo pequeño (p.ej. phi-4). Preguntas cortas y simples van al modelo
    pequeño; preguntas largas o que piden análisis/razonamiento van al
    modelo grande. No hay llamada a IA aquí -- es solo longitud del texto y
    coincidencia de palabras clave, para que el resultado sea determinista
    y fácil de explicar en la demo.
    """
    if not models:
        return ''
    if len(models) == 1:
        return models[0]
    large, small = models[0], models[-1]
    text = (prompt_text or '').strip()
    lowered = text.lower()
    is_long = len(text) > 220 or len(text.split()) > 40
    has_keyword = any(kw in lowered for kw in _COMPLEX_KEYWORDS)
    return large if (is_long or has_keyword) else small


def _load_dotenv(path):
    # Cargador mínimo de .env con la biblioteca estándar, solo para
    # desarrollo local (python app.py). En App Service y Container Apps no
    # existe ese archivo, así que esto no hace nada ahí — las variables las
    # inyecta la plataforma. setdefault() no pisa una variable que ya venga
    # del entorno real, para que esta siempre tenga prioridad sobre el .env.
    if not path.is_file():
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
            os.environ.setdefault(key, value)


def _stage_from_causa(causa):
    if causa in _PROMPT_STAGE_CAUSES:
        return 'prompt'
    if causa in (_RESPONSE_STAGE_CAUSE, _GATEWAY_REFUSAL_CAUSE):
        return 'response'
    return None


_CATEGORY_KEYS = ('Hate', 'Violence', 'Sexual', 'SelfHarm')

# Texto con el que APIM identifica un rechazo de llm-content-safety cuando
# responde con su error por defecto (sin pasar por el JSON del on-error).
_CONTENT_SAFETY_RE = re.compile(r'content[\s_-]*safety', re.IGNORECASE)


def _upstream_text(raw, limit=2000):
    """Cuerpo crudo de un error de APIM, recortado, para mostrarlo como evidencia."""
    if not raw:
        return ''
    text = raw.decode('utf-8', errors='replace').strip()
    return text[:limit] + ('…' if len(text) > limit else '')


def _int_map(raw, allowed=_CATEGORY_KEYS):
    """Convierte {categoria: numero} del JSON de policy.xml a {str: int}.

    Descarta claves desconocidas o valores no numéricos en vez de fallar: el
    cuerpo viene de APIM y no debe poder romper la respuesta al navegador.
    """
    out = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if k not in allowed or isinstance(v, bool):
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if 0 <= n <= 7:
            out[k] = n
    return out


def _header_json(headers, name):
    """Lee un header x-cs-* de policy.xml que trae JSON; None si no vino o no es válido."""
    try:
        raw = headers.get(name) if headers is not None else None
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _analysis(severities, thresholds, terms=None, blocklist_checked=True):
    """Misma forma de 'prompt'/'response' que usa la evidencia de un bloqueo,
    para que el front pinte las barras igual en solicitudes permitidas."""
    if not severities:
        return None
    terms = terms or []
    exceeded = [k for k, v in severities.items() if v >= thresholds.get(k, 7)]
    return {
        'categories': severities,
        'thresholds': thresholds,
        'blocklistMatches': len(terms),
        'blocklistTerms': terms[:20],
        'blocklistChecked': blocklist_checked,
        'maxSeverity': max(severities.values()),
        'allowedByThreshold': not exceeded,
    }


def _thresholds_label(thresholds):
    return ' · '.join(f'{k} ≥ {thresholds[k]}' for k in _CATEGORY_KEYS if k in thresholds)


def _blocked_diagnosis(detail, default_threshold):
    """Traduce el 403 estructurado de policy.xml a campos de evidencia.

    - Las severidades por categoría son del PROMPT (conversación completa),
      re-consultadas por el on-error. Se muestran siempre que existan: si la
      causa fue de entrada, explican el bloqueo; si fue la salida
      (salida_del_modelo_probable), muestran que la entrada pasó.
    - diagnostico_completo=false significa que alguna re-consulta falló. Si
      llegaron severidades, lo que falló fue Prompt Shield, así que su
      resultado se reporta como desconocido (None), no como "sin ataque".
    """
    causa = detail.get('causa_bloqueo') if isinstance(detail.get('causa_bloqueo'), str) else None
    causas = [c for c in (detail.get('causas_detectadas') or []) if isinstance(c, str)]
    if not causas and causa:
        causas = [causa]
    stage = _stage_from_causa(causa)
    thresholds = {k: default_threshold for k in _CATEGORY_KEYS}
    thresholds.update(_int_map(detail.get('umbrales_configurados')))
    severities = _int_map(detail.get('severidades_por_categoria'))
    terms = [str(t) for t in (detail.get('blocklist_coincidencias') or []) if isinstance(t, (str, int, float)) and not isinstance(t, bool)]
    complete = detail.get('diagnostico_completo') is True
    attack = detail.get('prompt_shield_ataque') is True

    prompt_analysis = _analysis(severities, thresholds, terms)

    if complete or attack:
        attack_detected = attack
    else:
        attack_detected = None

    raw_detail = detail.get('detalle_original_azure')
    return {
        'stage': stage,
        'causa': causa,
        'causas': causas,
        'thresholds': thresholds,
        'prompt': prompt_analysis,
        'promptAttackDetected': attack_detected,
        'diagnosisComplete': complete,
        'azureDetail': raw_detail[:500] if isinstance(raw_detail, str) else None,
        'riskLevel': detail.get('nivel_riesgo') if isinstance(detail.get('nivel_riesgo'), str) else None,
        'routedModel': detail.get('modelo_enrutado') if isinstance(detail.get('modelo_enrutado'), str) and detail.get('modelo_enrutado') else None,
        'gatewayRefusal': causa == _GATEWAY_REFUSAL_CAUSE,
    }


def create_app(settings=None, transport=None):
    config = dict(os.environ if settings is None else settings)
    endpoint = config.get('SAFETY_ENDPOINT', '').rstrip('/')
    key = config.get('SAFETY_API_KEY', '')
    # SAFETY_MODELS admite una lista separada por comas (los 2 modelos del
    # lab, p.ej. "gpt-5.6-luna,Phi-4") para que la interfaz ofrezca un
    # selector. SAFETY_MODEL sigue funcionando igual que antes -- un solo
    # modelo -- para no romper despliegues existentes que solo la usan a
    # ella.
    models_raw = config.get('SAFETY_MODELS', '') or config.get('SAFETY_MODEL', '')
    models = [m.strip() for m in models_raw.split(',') if m.strip()]
    model = models[0] if models else ''
    api_version = config.get('SAFETY_API_VERSION', '2024-05-01-preview')
    # SAFETY_THRESHOLD es SOLO informativo (lo que se muestra en la UI). El
    # umbral que de verdad aplica es el configurado en policy.xml
    # (llm-content-safety > categories > threshold); si cambias uno, cambia
    # el otro para que la UI no muestre un número distinto al real.
    threshold = int(config.get('SAFETY_THRESHOLD', '3'))
    if threshold not in range(8):
        raise ValueError('SAFETY_THRESHOLD debe estar entre 0 y 7.')
    # Umbrales POR CATEGORÍA, informativos igual que SAFETY_THRESHOLD. Deben
    # coincidir con csUmbrales y los threshold de policy.xml. Cuando APIM
    # responde, se usan los que manda en el header x-cs-umbral (o en
    # umbrales_configurados del 403), que son los reales.
    thresholds_cfg = {k: threshold for k in _CATEGORY_KEYS}
    for part in config.get('SAFETY_THRESHOLDS', 'Hate=2,Violence=3,Sexual=3,SelfHarm=3').split(','):
        name, _, value = part.partition('=')
        name, value = name.strip(), value.strip()
        if name in _CATEGORY_KEYS and value.isdigit() and int(value) in range(8):
            thresholds_cfg[name] = int(value)
    # threshold (un solo número) se mantiene por compatibilidad con el front:
    # es el umbral más estricto de todos.
    threshold = min(thresholds_cfg.values())
    # NOTA: SAFETY_CONTENT_ENDPOINT, SAFETY_CONTENT_KEY y
    # SAFETY_BLOCKLIST_NAME ya NO los usa esta app (antes servían para que
    # la app llamara a Content Safety por su cuenta, en paralelo a APIM;
    # ese control duplicado se quitó para que APIM sea el único punto de
    # decisión, tal como lo hace la política llm-content-safety). El
    # notebook del lab puede seguir escribiéndolas en .env sin problema:
    # simplemente no se leen aquí.
    # WEBSITE_HOSTNAME solo existe en Azure App Service. En Azure Container
    # Apps (y en cualquier otro host detrás de un proxy TLS) no se inyecta
    # automáticamente, así que se acepta PUBLIC_HOSTNAME como alternativa
    # explícita — se fija al desplegar el Container App con el FQDN público
    # de la revisión (p.ej. mi-app.<entorno>.<region>.azurecontainerapps.io).
    cloud_host = config.get('WEBSITE_HOSTNAME', '') or config.get('PUBLIC_HOSTNAME', '')
    configured = bool(endpoint and key and model)
    open_url = transport or urllib.request.urlopen
    if endpoint:
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('SAFETY_ENDPOINT debe ser HTTPS sin credenciales ni parámetros.')

    def _request_with_retry(req, retries=2, delay=1.2):
        # Azure AI Content Safety (y por extensión APIM, cuando reintenta
        # internamente) a veces devuelve errores transitorios (HTTP
        # 429/500/502/503/504). Un solo fallo transitorio no debe bloquear
        # la conversación: se reintenta un par de veces con una espera
        # corta. Un 403 (bloqueo real de contenido) NUNCA cae aquí porque
        # urllib solo lanza HTTPError, y ese código no está en la lista de
        # reintentables -- por diseño: no queremos reintentar un bloqueo.
        last_exc = None
        for attempt in range(retries + 1):
            try:
                with open_url(req, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code not in (429, 500, 502, 503, 504) or attempt == retries:
                    raise
                print(f'[gateway] intento {attempt + 1}/{retries + 1} falló con HTTP {exc.code} (transitorio); reintentando en {delay}s...', file=sys.stderr, flush=True)
            except urllib.error.URLError as exc:
                last_exc = exc
                if attempt == retries:
                    raise
                print(f'[gateway] intento {attempt + 1}/{retries + 1} falló ({exc.reason}); reintentando en {delay}s...', file=sys.stderr, flush=True)
            time.sleep(delay)
        raise last_exc  # pragma: no cover - inalcanzable, raise ocurre arriba

    def application(env, start_response):
        def send(status, data, mime='application/json; charset=utf-8', extra=()):
            payload = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, dict) else data
            headers = [('Content-Type', mime), ('Content-Length', str(len(payload))), ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'), ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")]
            start_response(f'{status} {HTTPStatus(status).phrase}', headers + list(extra))
            return [payload]

        path = env.get('PATH_INFO', '/')
        method = env.get('REQUEST_METHOD', 'GET')
        if path == '/health' and method == 'GET':
            return send(200, {'status': 'ok'})
        if method == 'GET':
            if path == '/api/status':
                return send(200, {'build': BUILD, 'configured': configured, 'evidenceConfigured': configured, 'model': model or 'Sin configurar', 'models': models, 'autoModelValue': _AUTO_MODEL_VALUE, 'endpoint': endpoint, 'threshold': threshold, 'thresholds': thresholds_cfg, 'thresholdsLabel': _thresholds_label(thresholds_cfg), 'protection': 'configured_unverified' if configured else 'unconfigured'})
            assets = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/styles.css': ('styles.css', 'text/css'), '/evidence.css': ('evidence.css', 'text/css')}
            if path not in assets:
                return send(404, {'error': 'No encontrado.'})
            filename, mime = assets[path]
            return send(200, (ROOT / filename).read_bytes(), mime + '; charset=utf-8')
        if path != '/api/chat':
            return send(404, {'error': 'No encontrado.'})
        if method != 'POST':
            return send(405, {'error': 'Método no permitido.'})
        host = env.get('HTTP_HOST', '')
        origin = env.get('HTTP_ORIGIN')
        valid_origins = {f'https://{cloud_host}'} if cloud_host else {f'http://{host}'}
        if origin and origin not in valid_origins:
            return send(403, {'error': 'Origen no permitido.'})
        if env.get('CONTENT_TYPE', '').split(';')[0] != 'application/json':
            return send(415, {'error': 'Se requiere JSON.'})
        try:
            length = int(env.get('CONTENT_LENGTH', '0'))
            if not 0 < length <= 150000:
                return send(413, {'error': 'La conversación supera el tamaño permitido. Inicia una nueva.'})
            data = json.loads(env['wsgi.input'].read(length))
            messages = data.get('messages') if isinstance(data, dict) else None
            if not isinstance(messages, list) or not 1 <= len(messages) <= 60:
                raise ValueError()
            for i, msg in enumerate(messages):
                if not isinstance(msg, dict) or msg.get('role') != ('user' if i % 2 == 0 else 'assistant'):
                    raise ValueError()
                text = msg.get('content')
                if not isinstance(text, str) or not text.strip() or len(text) > (4000 if msg['role'] == 'user' else 30000):
                    raise ValueError()
            if messages[-1]['role'] != 'user':
                raise ValueError()
            # Analyze all client-supplied context; never silently drop older messages.
            prompt_text = '\n'.join(m['content'] for m in messages)
            if len(prompt_text) > 10000:
                return send(413, {'error': 'El contexto supera 10.000 caracteres. Inicia una nueva conversación para analizarlo completo.'})
            # Modelo elegido en la interfaz (selector). "Automático" (o no
            # enviar model, o enviar uno que no está en SAFETY_MODELS) hace
            # que el servidor elija según la complejidad de la pregunta
            # (choose_model_auto) -- nunca se llama a un modelo que la
            # aplicación no tenga declarado explícitamente.
            requested_model = data.get('model') if isinstance(data, dict) else None
            if requested_model is not None and not isinstance(requested_model, str):
                raise ValueError()
            auto_selected = requested_model is None or requested_model == _AUTO_MODEL_VALUE or requested_model not in models
            last_user_text = messages[-1]['content']
            chosen_model = choose_model_auto(last_user_text, models) if auto_selected else requested_model
        except (ValueError, TypeError, UnicodeError):
            return send(400, {'error': 'El mensaje o el historial no es válido.'})
        if not configured:
            return send(503, {'error': 'Falta configurar la conexión con Azure API Management en App Service.', 'kind': 'configuration'})

        evidence = {
            'build': BUILD,
            'model': chosen_model,
            'modelSelection': 'automático' if auto_selected else 'manual',
            'modelSelectionReason': (
                ('Modelo grande: pregunta larga o con palabras que piden análisis/razonamiento.' if chosen_model == models[0] else 'Modelo pequeño: pregunta corta y directa.')
                if auto_selected and len(models) > 1 else
                ('Elegido manualmente en el selector.' if not auto_selected else None)
            ),
            'backend': 'Microsoft Foundry',
            'deployment': chosen_model,
            'gateway': 'Azure API Management',
            'policy': 'llm-content-safety',
            'phase': 'inbound + completions',
            'threshold': threshold,
            'promptShield': True,
            'completionEnforcement': True,
            'prompt': None,
            'response': None,
            'promptAttackDetected': None,
            'causaBloqueo': None,
            'causasDetectadas': [],
            'diagnosisComplete': None,
            'thresholds': dict(thresholds_cfg),
            'thresholdsLabel': _thresholds_label(thresholds_cfg),
            'requestedModel': chosen_model,
            'routedModel': None,
            'riskLevel': None,
            'riskLevelText': None,
            'gatewayRefusal': False,
            'azureDetail': None,
            'diagnosisMissing': False,
            'upstreamBody': None,
            'promptScope': 'Alcance: solo el mensaje actual (1 mensaje).' if len(messages) == 1 else f'Alcance: mensaje e historial ({len(messages)} mensajes).',
            'messageCount': len(messages),
            'gatewayProcessed': False,
            'policyApplied': False,
            'modelExecuted': False,
            'responseDelivered': False,
            'decisionSource': 'Azure API Management (política llm-content-safety)',
            'blockStage': None,
        }

        # Único punto de control: esta llamada a APIM es la que decide si el
        # prompt pasa o se bloquea. No hay ningún chequeo de Content Safety
        # antes de esto -- si APIM permite la solicitud, sigue hasta el
        # modelo; si la política la bloquea, APIM responde 403 y el modelo
        # nunca la recibe.
        payload = {'model': chosen_model, 'messages': [{'role': 'system', 'content': SYSTEM}] + [{'role': m['role'], 'content': m['content']} for m in messages], 'max_completion_tokens': 800}
        url = endpoint + '/chat/completions?api-version=' + urllib.parse.quote(api_version)
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json', 'api-key': key, 'Ocp-Apim-Subscription-Key': key, 'extra-parameters': 'pass-through'})
        try:
            with open_url(req, timeout=90) as response:
                result = json.load(response)
                response_headers = getattr(response, 'headers', {})
                response_status = getattr(response, 'status', 200)
            evidence.update(http=response_status, gatewayHttp=response_status, gatewayProcessed=True, policyApplied=True, modelExecuted=True, requestId=response_headers.get('apim-request-id') or response_headers.get('x-request-id'), servedModel=response_headers.get('x-ms-served-model'), region=response_headers.get('x-ms-region'))
            choice = result['choices'][0]
            if choice.get('finish_reason') == 'content_filter':
                # Distinto del bloqueo de la política llm-content-safety: es
                # el filtro de contenido propio del despliegue de Azure
                # OpenAI/Foundry actuando sobre la respuesta generada, no
                # Content Safety vía APIM.
                evidence.update(result='BLOCKED', http=403, blockStage='response', decisionSource='Filtro del backend LLM (Azure OpenAI/Foundry), no de la política llm-content-safety', reason='El backend devolvió finish_reason=content_filter; no es evidencia de un bloqueo de la política de APIM.')
                return send(403, {'error': evidence['reason'], 'kind': 'blocked', 'evidence': evidence})
            refusal = choice['message'].get('refusal')
            answer = choice['message'].get('content') or refusal
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError('Respuesta vacía')

            # Niveles que policy.xml expone en headers x-cs-* en TODAS las
            # solicitudes (no solo cuando bloquea): severidad de la entrada,
            # severidad de la respuesta, nivel de riesgo y modelo al que el
            # gateway enrutó. El gateway decide el modelo según la severidad,
            # así que puede ser distinto del que pidió la app.
            gw_thresholds = dict(thresholds_cfg)
            gw_thresholds.update(_int_map(_header_json(response_headers, 'x-cs-umbral')))
            prompt_sev = _int_map(_header_json(response_headers, 'x-cs-severidad-entrada'))
            response_sev = _int_map(_header_json(response_headers, 'x-cs-severidad-salida'))
            bl = _header_json(response_headers, 'x-cs-blocklist-entrada')
            bl_terms = [str(t) for t in bl if isinstance(t, (str, int, float)) and not isinstance(t, bool)] if isinstance(bl, list) else []
            risk_level = response_headers.get('x-cs-nivel') or None
            routed_model = response_headers.get('x-cs-modelo-enrutado') or None
            gateway_says_refusal = (response_headers.get('x-cs-negativa-modelo') or '').lower() == 'true'
            evidence.update(
                thresholds=gw_thresholds,
                thresholdsLabel=_thresholds_label(gw_thresholds),
                prompt=_analysis(prompt_sev, gw_thresholds, bl_terms),
                response=_analysis(response_sev, gw_thresholds, blocklist_checked=False),
                riskLevel=risk_level,
                riskLevelText=_RISK_LEVEL_TEXT.get(risk_level),
                routedModel=routed_model,
            )
            if routed_model:
                evidence.update(
                    model=routed_model,
                    deployment=routed_model,
                    modelSelection='gateway (por severidad)',
                    modelSelectionReason=_RISK_LEVEL_TEXT.get(risk_level) or 'El gateway eligió el modelo según la severidad de la entrada.',
                )
            # Si llegamos aquí con 200, la política llm-content-safety ya
            # validó la entrada Y, gracias a enforce-on-completions="true",
            # también esta respuesta antes de que APIM nos la devolviera. No
            # se vuelve a consultar Content Safety por separado: esta app ya
            # no tiene una llamada directa que pudiera repetir o contradecir
            # esa decisión. APIM no expone severidades por categoría cuando
            # PERMITE una solicitud (solo lo hace cuando bloquea, vía
            # on-error) -- por eso 'prompt' y 'response' quedan en None
            # aquí: no se inventan puntuaciones que Azure no entregó.
            #
            # El campo estructurado `refusal` casi nunca viene poblado (lo
            # entregan pocos modelos/versiones). Si no vino pero el texto
            # arranca como una negativa típica, se marca igual como
            # MODEL_REFUSAL -- pero por una vía distinta (heurística de
            # texto, no el campo estructurado), y la razón mostrada lo dice
            # explícitamente para no presentarlo con la misma certeza.
            refusal_detection = 'structured' if refusal else ('heuristic' if (_looks_like_refusal(answer) or gateway_says_refusal) else None)
            is_refusal = refusal_detection is not None
            nivel_txt = ''
            if prompt_sev:
                top = max(prompt_sev, key=prompt_sev.get)
                nivel_txt = f' Nivel máximo de la entrada: {prompt_sev[top]} en {top} (umbral {gw_thresholds.get(top)}).'
            if refusal_detection == 'heuristic':
                # Con la política actual, una negativa con entrada de riesgo
                # llega como 403 (negativa_del_modelo_con_riesgo). Si llega
                # aquí con 200, la entrada estaba limpia.
                reason = 'La política llm-content-safety de APIM validó la entrada y la respuesta; ninguna alcanzó su umbral y la entrada no tenía riesgo, así que el gateway no la convirtió en bloqueo. El modelo decidió no responder por su propio criterio (detectado por coincidencia de frases, no por un campo estructurado de la API).' + nivel_txt
            else:
                reason = 'La política llm-content-safety de APIM validó la entrada y, gracias a enforce-on-completions, también la respuesta antes de entregarla. Los niveles por categoría que se muestran los midió el gateway y los devolvió en los headers x-cs-*.' + nivel_txt
            evidence.update(result='MODEL_REFUSAL' if is_refusal else 'ALLOWED', http=200, responseDelivered=True, refusalDetection=refusal_detection, reason=reason)
            return send(200, {'content': answer, 'evidence': evidence})
        except urllib.error.HTTPError as exc:
            raw = exc.read(65536)
            try:
                detail = json.loads(raw.decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                detail = None
            if isinstance(detail, dict) and detail.get('blocked') is True:
                # Bloqueo real de la política llm-content-safety, con el
                # diagnóstico que arma el on-error de policy.xml. Esta es la
                # ÚNICA fuente de la decisión -- no se re-verifica con una
                # llamada propia a Content Safety.
                diag = _blocked_diagnosis(detail, threshold)
                stage = diag['stage']
                evidence.update(
                    result='BLOCKED',
                    http=403,
                    gatewayHttp=exc.code,
                    gatewayProcessed=True,
                    policyApplied=True,
                    modelExecuted=(stage == 'response') if stage else None,
                    requestId=exc.headers.get('apim-request-id') or exc.headers.get('x-request-id'),
                    servedModel=None,
                    region=exc.headers.get('x-ms-region'),
                    blockStage=stage,
                    causaBloqueo=diag['causa'],
                    causasDetectadas=diag['causas'],
                    diagnosisComplete=diag['diagnosisComplete'],
                    thresholds=diag['thresholds'],
                    azureDetail=diag['azureDetail'],
                    prompt=diag['prompt'],
                    promptAttackDetected=diag['promptAttackDetected'],
                    thresholdsLabel=_thresholds_label(diag['thresholds']),
                    riskLevel=diag['riskLevel'],
                    riskLevelText=_RISK_LEVEL_TEXT.get(diag['riskLevel']),
                    routedModel=diag['routedModel'],
                    gatewayRefusal=diag['gatewayRefusal'],
                    reason=detail.get('mensaje') or 'La política llm-content-safety de APIM bloqueó la solicitud.',
                )
                if diag['routedModel']:
                    evidence.update(model=diag['routedModel'], deployment=diag['routedModel'], modelSelection='gateway (por severidad)', modelSelectionReason=_RISK_LEVEL_TEXT.get(diag['riskLevel']))
                if diag['gatewayRefusal']:
                    # El modelo sí se ejecutó y se negó; el gateway convirtió
                    # esa negativa en bloqueo porque la entrada tenía riesgo.
                    evidence.update(
                        modelExecuted=True,
                        promptAttackDetected=False,
                        decisionSource='Azure API Management (policy.xml: negativa del modelo con entrada de riesgo)',
                    )
                return send(403, {'error': evidence['reason'], 'kind': 'blocked', 'evidence': evidence})
            if isinstance(detail, dict) and detail.get('servicio_no_disponible') is True:
                # Error transitorio de Azure AI Content Safety (visto desde
                # APIM), no un bloqueo de contenido.
                evidence.update(result='ERROR', http=503, gatewayHttp=exc.code, gatewayProcessed=True, policyApplied=None, modelExecuted=None, reason=detail.get('mensaje') or 'El servicio de Azure AI Content Safety no respondió. Reintenta en unos segundos.')
                return send(503, {'error': evidence['reason'], 'kind': 'safety_unavailable', 'evidence': evidence})
            # Cualquier otro error HTTP de APIM/backend, sin la forma que
            # arma policy.xml -- no se adivina que fue un bloqueo de
            # contenido a partir de texto suelto en el cuerpo.
            upstream_text = _upstream_text(raw)
            request_id = exc.headers.get('apim-request-id') or exc.headers.get('x-request-id')
            if exc.code == 403 and _CONTENT_SAFETY_RE.search(upstream_text or ''):
                # APIM bloqueó por llm-content-safety pero respondió con su
                # error POR DEFECTO, no con el JSON del on-error de
                # policy.xml (p. ej. policy sin redesplegar, o el on-error no
                # entró en su rama). Es un bloqueo real de la política -- el
                # texto de APIM lo dice -- pero sin diagnóstico: no hay
                # severidades, blocklist ni Prompt Shield que mostrar.
                evidence.update(
                    result='BLOCKED', http=403, gatewayHttp=exc.code, gatewayProcessed=True, policyApplied=True,
                    modelExecuted=None, requestId=request_id, region=exc.headers.get('x-ms-region'),
                    blockStage=None, causaBloqueo='no_determinada', causasDetectadas=['no_determinada'],
                    diagnosisComplete=False, diagnosisMissing=True, upstreamBody=upstream_text,
                    reason='La política llm-content-safety de APIM bloqueó la solicitud, pero APIM respondió con su error por defecto en lugar del diagnóstico de policy.xml, así que no hay categoría ni severidad que mostrar.',
                )
                return send(403, {'error': evidence['reason'], 'kind': 'blocked', 'evidence': evidence})
            errors = {401: 'La credencial de API Management no es válida.', 403: 'El gateway denegó el acceso. Revisa permisos y políticas; este estado no confirma un bloqueo de contenido.', 404: 'No se encontró la ruta o el modelo. Revisa la configuración del laboratorio.', 429: 'Se alcanzó el límite de solicitudes o cuota. Intenta de nuevo más tarde.'}
            evidence.update(result='ERROR', http=502, gatewayHttp=exc.code, gatewayProcessed=True, modelExecuted=None, policyApplied=None, requestId=request_id, upstreamBody=upstream_text, reason=errors.get(exc.code, 'Error del servicio de Azure. No equivale a un bloqueo de contenido.'))
            return send(502, {'error': evidence['reason'], 'upstream_status': exc.code, 'kind': 'upstream', 'evidence': evidence})
        except (urllib.error.URLError, TimeoutError):
            evidence.update(result='ERROR', http=504, gatewayProcessed=None, policyApplied=None, modelExecuted=None, reason='No se recibió respuesta de Azure; ejecución del modelo desconocida.')
            return send(504, {'error': evidence['reason'], 'kind': 'connection', 'evidence': evidence})
        except (ValueError, KeyError, IndexError, TypeError):
            evidence.update(result='ERROR', http=502, reason='Azure devolvió una respuesta no utilizable; no se entrega contenido.')
            return send(502, {'error': evidence['reason'], 'kind': 'upstream', 'evidence': evidence})
    return application


_load_dotenv(ROOT / '.env')
app = create_app()

if __name__ == '__main__':
    from wsgiref.simple_server import make_server
    # WEBSITE_HOSTNAME (App Service) o CONTAINER (fijada por el Dockerfile,
    # para docker run/Container Apps) => hay que escuchar en todas las
    # interfaces para que el mapeo de puertos o el ingress puedan alcanzar
    # el proceso. PUBLIC_HOSTNAME es aparte: solo endurece la validación de
    # origen una vez que se conoce el hostname público real (ver create_app).
    listen_all = bool(os.getenv('WEBSITE_HOSTNAME') or os.getenv('CONTAINER') or os.getenv('PUBLIC_HOSTNAME'))
    host = '0.0.0.0' if listen_all else '127.0.0.1'
    port = int(os.getenv('PORT', '8000' if listen_all else '8765'))
    with make_server(host, port, app) as server:
        print(f'Servidor disponible en http://{host}:{server.server_port}', flush=True)
        server.serve_forever()