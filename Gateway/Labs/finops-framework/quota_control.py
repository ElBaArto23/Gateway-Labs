"""
quota_control.py — Control de cuotas de subscription1-4 para la demo de
failover del FinOps Framework dashboard.

No depende del notebook ni de Jupyter. Requiere:
  - Azure CLI instalado y logueado (`az login`) con acceso al resource group
    del lab (lab-finops-framework-V24) y al Log Analytics workspace.
  - Paquetes Python: azure-identity, azure-monitor-ingestion
    (ya deberían estar instalados si corriste `uv sync` para el lab).

Uso:
    python quota_control.py demo       # aplica cuotas bajas para la demo
    python quota_control.py restore    # restaura las cuotas normales
    python quota_control.py status     # solo muestra qué se aplicaría, sin subir nada

Después de correr 'demo' o 'restore', espera unos minutos (delay de
ingesta de Log Analytics) antes de verificar en el dashboard.
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone

DEPLOYMENT_NAME = "finops-framework"
RESOURCE_GROUP = "lab-finops-framework-V24"

# Suscripciones y su producto normal (usado para calcular la cuota "restore").
SUBSCRIPTIONS = ["subscription1", "subscription2", "subscription3", "subscription4"]
NORMAL_PRODUCT_QUOTA = {
    "subscription1": 15,     # platinum
    "subscription2": 10,     # gold
    "subscription3": 5,      # silver
    "subscription4": 5,      # silver
}

# Cuotas bajas pensadas para que la demo muestre la cascada completa en
# pocos mensajes de "Probar Gateway" (costo real ≈ $0.0002-0.0003/mensaje).
DEMO_QUOTA = {
    # Recalibrado el 07/09/2026 tras la migración al workspace compartido
    # (workspace-pdcibwky2f5ms, RG rg-shared-apim-gateway-V2). Los cálculos
    # de 21/08/2026 quedaron obsoletos porque estaban contra el workspace
    # privado viejo (workspace-7ruu2e27nelzk), que ya no existe.
    #
    # Base: TotalCostMes real (mes calendario completo, startofmonth/
    # endofmonth) verificado por KQL directo en workspace-pdcibwky2f5ms,
    # con el mismo filtro que usa llm_logs_month_to_date() en app.py
    # (ApiId de finops-framework + join deduplicado + precios de
    # PRICING_CL) — o sea, exactamente la base que compara CostQuota en
    # el dashboard: sub1=$0.00385, sub2=$0.00399, sub3=$0.01543,
    # sub4=$0.03870.
    #
    # Margen ajustado (~$0.005-0.006 sobre lo acumulado, a propósito
    # chico): con costo real ≈ $0.0002-0.0003/mensaje, cada suscripción
    # queda a ~15-20 mensajes de "Probar Gateway" de mostrarse excedida,
    # para que la cascada de failover se vea completa en una demo corta.
    "subscription1": 0.009,     # platinum — acumulado 0.00385 + margen ajustado
    "subscription2": 0.009,     # gold — acumulado 0.00399 + margen ajustado
    "subscription3": 0.021,     # silver — acumulado 0.01543 + margen ajustado
    "subscription4": 0.044,     # silver — acumulado 0.03870 + margen ajustado
}

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5


def run(cmd: list[str]) -> str:
    # En Windows, `az` es en realidad az.cmd; subprocess necesita shell=True
    # para encontrarlo (igual que cuando lo escribes directo en PowerShell).
    use_shell = sys.platform.startswith("win")
    result = subprocess.run(cmd, capture_output=True, text=True, shell=use_shell)
    if result.returncode != 0:
        raise RuntimeError(f"Comando falló: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout


def get_deployment_outputs() -> dict:
    print(f"→ Leyendo outputs del deployment '{DEPLOYMENT_NAME}' en '{RESOURCE_GROUP}'...")
    raw = run([
        "az", "deployment", "group", "show",
        "--name", DEPLOYMENT_NAME,
        "-g", RESOURCE_GROUP,
        "-o", "json",
    ])
    data = json.loads(raw)
    outputs = data["properties"]["outputs"]
    return {
        "endpoint": outputs["subscriptionQuotaDCREndpoint"]["value"],
        "immutable_id": outputs["subscriptionQuotaDCRImmutableId"]["value"],
        "stream": outputs["subscriptionQuotaDCRStream"]["value"],
    }


def upload_quota(client, dcr, subscription: str, cost_quota: float) -> bool:
    from azure.core.exceptions import HttpResponseError

    body = [{
        "TimeGenerated": str(datetime.now(timezone.utc)),
        "Subscription": subscription,
        "Email": None,
        "CostQuota": cost_quota,
    }]

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            client.upload(rule_id=dcr["immutable_id"], stream_name=dcr["stream"], logs=body)
            print(f"  ✓ {subscription} -> CostQuota={cost_quota}")
            return True
        except HttpResponseError as e:
            print(f"  ✗ intento {attempt}/{MAX_RETRIES} falló para {subscription}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    print(f"  ✗✗ {subscription} NO se pudo actualizar tras {MAX_RETRIES} intentos.")
    return False


def apply_quotas(mode: str):
    from azure.identity import DefaultAzureCredential
    from azure.monitor.ingestion import LogsIngestionClient

    quotas = DEMO_QUOTA if mode == "demo" else NORMAL_PRODUCT_QUOTA
    label = "DEMO (bajas)" if mode == "demo" else "NORMALES (restauradas)"

    print(f"\n=== Aplicando cuotas {label} ===")
    for sub in SUBSCRIPTIONS:
        print(f"  {sub}: CostQuota -> {quotas[sub]}")
    confirm = input("\n¿Continuar? [s/N]: ").strip().lower()
    if confirm != "s":
        print("Cancelado. No se subió nada.")
        return

    dcr = get_deployment_outputs()
    credential = DefaultAzureCredential()
    client = LogsIngestionClient(endpoint=dcr["endpoint"], credential=credential, logging_enable=False)

    print("\n→ Subiendo a Log Analytics...")
    results = {sub: upload_quota(client, dcr, sub, quotas[sub]) for sub in SUBSCRIPTIONS}

    print("\n=== Resumen ===")
    ok = [s for s, success in results.items() if success]
    failed = [s for s, success in results.items() if not success]
    print(f"OK: {ok if ok else '(ninguna)'}")
    if failed:
        print(f"FALLARON: {failed}  <-- vuelve a correr el script para reintentar solo estas.")
        sys.exit(1)
    else:
        print("Todas las suscripciones actualizadas correctamente.")
        print("Espera unos minutos (delay de ingesta) antes de verificar en el dashboard.")


def show_status():
    print("\n=== Cuotas que se aplicarían ===")
    print("\n-- demo --")
    for sub in SUBSCRIPTIONS:
        print(f"  {sub}: {DEMO_QUOTA[sub]}")
    print("\n-- restore (normal) --")
    for sub in SUBSCRIPTIONS:
        print(f"  {sub}: {NORMAL_PRODUCT_QUOTA[sub]}")
    print("\nNada se sube en este modo. Usa 'demo' o 'restore' para aplicar.")


def main():
    parser = argparse.ArgumentParser(description="Control de cuotas de demo para el FinOps Framework dashboard.")
    parser.add_argument("mode", choices=["demo", "restore", "status"], help="Qué hacer.")
    args = parser.parse_args()

    if args.mode == "status":
        show_status()
    else:
        apply_quotas(args.mode)


if __name__ == "__main__":
    main()