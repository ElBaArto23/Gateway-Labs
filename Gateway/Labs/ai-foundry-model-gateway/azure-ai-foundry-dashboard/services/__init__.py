"""
services/__init__.py
====================
Paquete de servicios para AI Foundry Model Gateway.
"""

from .foundry_service import get_project_client, get_agent, send_message, ask_agent
from .cost_calculator import calculate_cost, get_model_pricing
from .usage_tracker import record_usage, get_consumption_summary, check_budget, BudgetExceededError
from .event_logger import EventLogger
from .metrics_collector import record_metric, get_aggregated_metrics
from .gateway_metrics import record_gateway_event, get_gateway_telemetry
from .dashboard_service import get_dashboard_summary
from .charts_service import generate_chart_data
from .alerts_service import check_alerts