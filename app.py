"""Flask Dashboard API"""

import csv
import logging
import os
import threading
from typing import Optional

from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, request, redirect, session, url_for
from flask_cors import CORS
from flask_socketio import SocketIO

from analytics.edge_diagnostics import build_edge_diagnostics
from analytics.forward_validation import build_validation_report, current_strategy_context
from analytics.ict_diagnostics import load_dashboard_payload
from analytics.performance import summarize_performance
from alerts.manager import alert_manager
from brokers import get_broker_manager
from engine import TradingEngine
from licensing import current_machine_identity, get_license_manager
from journal.writer import strategy_journal
from risk.monitor import RiskMonitor
from tenants.isolation import get_active_tenant_id, get_tenant_paths
from users.auth import authenticate_user, context_permissions, current_user_context, is_saas_mode, login_context, logout_context, require_permission, require_role
from users.store import get_user_store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

load_dotenv(ENV_PATH)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "nexus-dev-secret-change-me"
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
CORS(app)
socketio = SocketIO(app, cors_allowed_origins='*')


def _emit_alert(alert: dict):
    socketio.emit("alert", alert, namespace="/")


alert_manager.set_emitter(_emit_alert)

# Global engine instance (shared state between API and background loop)
engine: Optional[TradingEngine] = None
_realtime_thread: Optional[threading.Thread] = None
_engine_thread: Optional[threading.Thread] = None
_engine_lock = threading.RLock()  # CRITICAL: Prevent race conditions on global engine

PUBLIC_ENDPOINTS = {
    "login",
    "about",
    "license_terms",
    "api_auth_login",
    "api_auth_logout",
    "api_auth_me",
    "static",
}

NEXUS_BRANDING = {
    "owner_name": "Eliud Karanja Ndiritu",
    "brand": "SAJ / Nexus Trading Systems",
    "role": "Trading Automation Specialist | Forex & Crypto Bot Systems | MT5 Integration",
    "email": "eliudkaranja5@gmail.com",
    "phone": "+254 729 576 473",
    "whatsapp": "+254 702 839 859",
    "profile": (
        "Eliud Karanja Ndiritu is a software developer and trading automation specialist focused on "
        "building intelligent trading systems, MT5 bot integrations, risk-controlled automation, broker "
        "connectivity, dashboard analytics, and licensing systems for Forex, Gold, and Crypto trading platforms."
    ),
    "footer": (
        "Powered by Nexus Trading Systems | Developed by Eliud Karanja Ndiritu | "
        "Email: eliudkaranja5@gmail.com | Call: +254 729 576 473 | WhatsApp: +254 702 839 859"
    ),
    "license_notice": "Licensed Software - Unauthorized copying, resale, modification, or redistribution is prohibited.",
    "risk_disclaimer": (
        "Trading forex, CFDs, gold, indices, and crypto involves significant risk. Automated trading systems "
        "can lose money. Past performance, backtests, and demo results do not guarantee future profits. "
        "Users are responsible for their own trading decisions and risk management."
    ),
}


@app.context_processor
def inject_branding():
    return {"branding": NEXUS_BRANDING}


def _wants_json_response() -> bool:
    return request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", "")


@app.before_request
def enforce_saas_login():
    """Require a logged-in session only when SaaS mode is explicitly enabled."""
    if not is_saas_mode():
        return None
    endpoint = request.endpoint or ""
    if endpoint in PUBLIC_ENDPOINTS or request.path.startswith("/static/"):
        return None
    context = current_user_context()
    if context.is_authenticated:
        return None
    if _wants_json_response():
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    return redirect(url_for("login", next=request.full_path if request.query_string else request.path))


# CRITICAL FIX: Input validation for API endpoints
def validate_float_param(value, param_name: str, min_val=None, max_val=None):
    """CRITICAL FIX: Safely validate float parameters"""
    try:
        val = float(value)
        if min_val is not None and val < min_val:
            return None, f"{param_name} must be >= {min_val}, got {val}"
        if max_val is not None and val > max_val:
            return None, f"{param_name} must be <= {max_val}, got {val}"
        return val, None
    except (TypeError, ValueError):
        return None, f"{param_name} must be a valid number"


def validate_symbols_param(value):
    """CRITICAL FIX: Safely validate and sanitize symbols"""
    if isinstance(value, str):
        symbols = [s.strip().upper() for s in value.split(",") if s.strip()]
    elif isinstance(value, list):
        symbols = [str(s).strip().upper() for s in value if s]
    else:
        return None, "Symbols must be string or array"
    
    if not symbols:
        return None, "Symbols cannot be empty"
    return symbols, None


def read_env_file(path=ENV_PATH):
    env_vars = {}
    if os.path.exists(path):
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env_vars[key] = val
    return env_vars


def set_env_file_value(key: str, value: str, path=ENV_PATH):
    lines = []
    found = False
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle.readlines():
                if line.strip().startswith(f"{key}="):
                    lines.append(f"{key}={value}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")
    with open(path, "w", encoding="utf-8") as handle:
        handle.writelines(lines)
    os.environ[key] = str(value)


def ensure_runtime_files():
    """Create runtime data files that dashboard/API readers expect."""
    data_dir = os.path.join(BASE_DIR, "data")
    os.makedirs(data_dir, exist_ok=True)

    jsonl_files = [
        "alerts.jsonl",
        "strategy_journal.jsonl",
        "forward_trades.jsonl",
    ]
    for filename in jsonl_files:
        path = os.path.join(data_dir, filename)
        if not os.path.exists(path):
            open(path, "a", encoding="utf-8").close()

    csv_files = {
        "trades.csv": [
            "timestamp", "symbol", "direction", "entry", "exit", "sl", "tp",
            "volume", "profit", "r_multiple", "reason",
        ],
        "equity_curve.csv": ["timestamp", "equity", "balance", "drawdown"],
    }
    for filename, headers in csv_files.items():
        path = os.path.join(data_dir, filename)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(headers)

    json_files = {
        "adaptive_weights.json": {"cooldown_until": None, "toxic_symbols": []},
    }
    for filename, payload in json_files.items():
        path = os.path.join(data_dir, filename)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "w", encoding="utf-8") as handle:
                import json
                json.dump(payload, handle, indent=2)


ensure_runtime_files()
license_manager = get_license_manager()
broker_manager = get_broker_manager()
user_store = get_user_store()


@app.errorhandler(404)
def handle_not_found(error):
    if request.path.startswith("/api/"):
        return jsonify({"status": "error", "message": "Endpoint not found"}), 404
    return error


@app.errorhandler(405)
def handle_method_not_allowed(error):
    if request.path.startswith("/api/"):
        return jsonify({"status": "error", "message": "Method not allowed"}), 405
    return error


@app.errorhandler(500)
def handle_internal_error(error):
    if request.path.startswith("/api/"):
        return jsonify({"status": "error", "message": "Internal server error"}), 500
    return error


@app.route("/")
def index():
    app_js_path = os.path.join(app.static_folder, "js", "app.js")
    app_css_path = os.path.join(app.static_folder, "css", "style.css")
    try:
        app_js_version = int(os.path.getmtime(app_js_path))
    except Exception:
        app_js_version = 1
    try:
        app_css_version = int(os.path.getmtime(app_css_path))
    except Exception:
        app_css_version = 1
    ui_version = max(app_js_version, app_css_version)
    return render_template(
        "index.html",
        app_js_version=app_js_version,
        app_css_version=app_css_version,
        ui_version=ui_version,
        user_context=current_user_context(),
    )


@app.route("/login", methods=["GET"])
def login():
    if not is_saas_mode():
        return redirect(url_for("index"))
    if current_user_context().is_authenticated:
        return redirect(request.args.get("next") or url_for("index"))
    return render_template("login.html")


@app.route("/about", methods=["GET"])
def about():
    return render_template("public_portal.html", page="about")


@app.route("/license-terms", methods=["GET"])
def license_terms():
    return render_template("license_terms.html")


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    if not is_saas_mode():
        return jsonify({"status": "success", "message": "Local mode does not require login"})
    payload = request.get_json(silent=True) or request.form or {}
    context = authenticate_user(payload.get("email"), payload.get("password"))
    if not context:
        return jsonify({"status": "error", "message": "Invalid email or password"}), 401
    login_context(context)
    return jsonify({
        "status": "success",
        "data": {
            "user": context.user.to_public_dict() if context.user else None,
            "tenant_id": context.tenant_id,
            "permissions": context_permissions(context),
        },
    })


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    logout_context()
    return jsonify({"status": "success"})


@app.route("/api/auth/me", methods=["GET"])
def api_auth_me():
    context = current_user_context()
    return jsonify({
        "status": "success",
        "data": {
            "saas_mode": context.saas_mode,
            "authenticated": context.is_authenticated,
            "tenant_id": context.tenant_id,
            "user": context.user.to_public_dict() if context.user else None,
            "permissions": context_permissions(context),
        },
    })


@app.route("/api/users", methods=["GET"])
@require_permission("users")
def api_users():
    try:
        return jsonify({"status": "success", "data": user_store.list_users()})
    except Exception as e:
        logger.exception("User list failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/users/create", methods=["POST"])
@require_permission("users")
def api_users_create():
    try:
        data = request.get_json(silent=True) or {}
        created = user_store.create_user(
            email=data.get("email"),
            password=data.get("password"),
            role=data.get("role"),
            tenant_id=data.get("tenant_id"),
        )
        return jsonify({"status": "success", "data": created})
    except Exception as e:
        logger.exception("User create failed")
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/users/update", methods=["POST"])
@require_permission("users")
def api_users_update():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data.get("id") or 0)
        if not user_id:
            return jsonify({"status": "error", "message": "User id is required"}), 400
        updated = user_store.update_user(
            user_id,
            role=data.get("role") if "role" in data else None,
            is_active=data.get("is_active") if "is_active" in data else None,
            password=data.get("password") or None,
        )
        return jsonify({"status": "success", "data": updated})
    except Exception as e:
        logger.exception("User update failed")
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/branding/integrity", methods=["POST"])
def api_branding_integrity():
    data = request.get_json(silent=True) or {}
    intact = bool(data.get("intact"))
    session["branding_integrity_ok"] = intact
    return jsonify({
        "status": "success" if intact else "warning",
        "data": {"intact": intact},
        "message": "Branding integrity verified" if intact else "Licensed branding integrity warning",
    })


@app.route("/api/bot/start", methods=["POST"])
@require_permission("trade")
def api_start():
    global engine, _engine_thread
    with _engine_lock:  # CRITICAL: Prevent race condition on engine creation
        try:
            if engine and engine.is_running:
                return jsonify({"status": "error", "message": "Bot already running"}), 400
            if session.get("branding_integrity_ok") is False:
                return jsonify({
                    "status": "error",
                    "message": "Licensed branding integrity warning: live trading start is disabled for this session.",
                }), 403

            payload = request.json or {}
            rule_config = {
                "ema": payload.get("ema", True),
                "volume": payload.get("volume", True),
                "po3": payload.get("po3", True),
            }

            engine = TradingEngine()
            if engine.startup_validation_error:
                message = engine.startup_validation_error
                logger.error(message)
                engine = None
                return jsonify({"status": "error", "message": message}), 400
            engine.rule_config.update(rule_config)

            # CRITICAL FIX: Validate and sanitize symbols
            if "symbols" in payload and payload.get("symbols") not in (None, "", []):
                symbols, error = validate_symbols_param(payload["symbols"])
                if error:
                    return jsonify({"status": "error", "message": f"Invalid symbols: {error}"}), 400
                engine.configured_symbols = list(symbols)
                engine.symbols = symbols
                engine.startup_validation_error = engine._validate_broker_symbol_compatibility()
                if engine.startup_validation_error:
                    message = engine.startup_validation_error
                    logger.error(message)
                    engine = None
                    return jsonify({"status": "error", "message": message}), 400

            # CRITICAL FIX: Validate volume parameter
            if "volume" in payload:
                volume, error = validate_float_param(payload["volume"], "volume", min_val=0.01, max_val=10)
                if error:
                    return jsonify({"status": "error", "message": error}), 400
                if volume is not None:
                    engine.volume = volume

            # CRITICAL FIX: Validate risk percentage parameter
            risk_pct = payload.get("risk_pct") or payload.get("RISK_PERCENT")
            if risk_pct is not None:
                risk_val, error = validate_float_param(risk_pct, "risk_pct", min_val=0.001, max_val=1)
                if error:
                    return jsonify({"status": "error", "message": error}), 400
                if risk_val is not None:
                    engine.risk_pct = risk_val

            # CRITICAL FIX: Validate max exposure percentage parameter
            if "max_exposure_pct" in payload:
                exposure, error = validate_float_param(payload["max_exposure_pct"], "max_exposure_pct", min_val=0.01, max_val=1)
                if error:
                    return jsonify({"status": "error", "message": error}), 400
                if exposure is not None:
                    engine.max_exposure_pct = exposure

            if not engine.connect():
                broker_name = (engine.broker_profile or {}).get("name") or (engine.broker_profile or {}).get("broker_type") or "broker"
                alert_manager.create(
                    "Broker connection failed",
                    f"Bot start failed because {broker_name} could not connect. Check credentials, profile mode, and broker server.",
                    severity="danger",
                    category="execution",
                    event="broker_connect_failed",
                    dedupe_key="app:broker_connect_failed",
                    cooldown_seconds=60,
                )
                engine = None
                return jsonify({"status": "error", "message": f"Broker connection failed: {broker_name}"}), 500

            _engine_thread = threading.Thread(target=engine.start, daemon=True)
            _engine_thread.start()
            _start_realtime_thread()
            alert_manager.create(
                "Bot started",
                "Nexus Trading Bot started successfully.",
                severity="success",
                category="system",
                event="bot_started",
            )
            return jsonify({"status": "success", "message": "Bot started"})
        except Exception as e:
            logger.error(f"Error starting bot: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/bot/stop", methods=["POST"])
@require_permission("trade")
def api_stop():
    global engine, _engine_thread
    with _engine_lock:  # CRITICAL: Prevent race condition on engine shutdown
        try:
            if engine:
                engine.stop()
                engine.disconnect()
                if _engine_thread and _engine_thread.is_alive():
                    _engine_thread.join(timeout=2)
                engine = None
                _engine_thread = None
                alert_manager.create(
                    "Bot stopped",
                    "Nexus Trading Bot stopped and disconnected from the active broker.",
                    severity="info",
                    category="system",
                    event="bot_stopped",
                )
                return jsonify({"status": "success", "message": "Bot stopped"})
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500


def _build_realtime_payload():
    global engine
    if not engine:
        return None

    with _engine_lock:
        status = engine.get_status() or {}
        signals = {
            "recent": engine.recent_signals if hasattr(engine, 'recent_signals') else [],
            "favorable": engine.favorable_signals if hasattr(engine, 'favorable_signals') else [],
        }
        pending = []
        try:
            pending = engine.pending_order_manager.get_pending_orders_summary() if hasattr(engine, 'pending_order_manager') else []
        except Exception:
            pending = []

        positions = []
        try:
            positions = engine.mt5.get_positions() if hasattr(engine, 'mt5') else []
        except Exception:
            positions = []

        logs = {
            "rejections": engine.rejection_logs[-50:] if hasattr(engine, 'rejection_logs') else [],
            "trades": engine.logger.get_logs() if hasattr(engine, 'logger') else [],
            "signals": engine.signal_history[-50:] if hasattr(engine, 'signal_history') else [],
            "future_trades": engine.future_trades[-50:] if hasattr(engine, 'future_trades') else [],
        }

        stats = engine.logger.get_stats() if hasattr(engine, 'logger') else {}
        alerts = alert_manager.list(limit=25)
        risk = RiskMonitor(engine).status()

        return {
            "status": status,
            "signals": signals,
            "scanner_debug": engine.get_scanner_debug() if hasattr(engine, "get_scanner_debug") else {},
            "pending_orders": pending,
            "positions": positions,
            "logs": logs,
            "stats": stats,
            "alerts": alerts,
            "risk": risk,
        }


def _start_realtime_thread():
    global _realtime_thread

    if _realtime_thread and _realtime_thread.is_alive():
        return

    def _realtime_worker():
        import time
        interval = max(2.0, float(os.getenv("REALTIME_PUSH_SECONDS", "5")))

        while True:
            time.sleep(interval)
            payload = _build_realtime_payload()
            if payload:
                try:
                    socketio.emit('dashboard_update', payload, namespace='/')
                except Exception as e:
                    logger.warning(f"SocketIO emit failed: {e}")

    _realtime_thread = threading.Thread(target=_realtime_worker, daemon=True)
    _realtime_thread.start()


@app.route("/api/alerts", methods=["GET"])
def api_alerts():
    try:
        limit = request.args.get("limit", 100)
        return jsonify({"status": "success", "data": alert_manager.list(limit=limit)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/alerts/clear", methods=["POST"])
@require_permission("trade")
def api_alerts_clear():
    try:
        cleared = alert_manager.clear()
        alert_manager.create(
            "Alerts cleared",
            f"Cleared {cleared} alert(s).",
            severity="info",
            category="system",
            event="alerts_cleared",
        )
        return jsonify({"status": "success", "message": f"Cleared {cleared} alert(s)"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/tenant/context", methods=["GET"])
def api_tenant_context():
    """Expose tenant mode metadata without requiring login in local mode."""
    try:
        tenant_id = get_active_tenant_id()
        tenant_paths = get_tenant_paths(tenant_id)
        return jsonify({
            "status": "success",
            "data": {
                "saas_mode": is_saas_mode(),
                "tenant_id": tenant_id,
                "roles": ["admin", "trader", "viewer"],
                "permissions": context_permissions(current_user_context()),
                "paths": {
                    "root": tenant_paths.root,
                    "config_path": tenant_paths.config_path,
                    "mt5_credentials_path": tenant_paths.mt5_credentials_path,
                } if is_saas_mode() else None,
            },
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/risk/status", methods=["GET"])
def api_risk_status():
    with _engine_lock:
        try:
            return jsonify({"status": "success", "data": RiskMonitor(engine).status()})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/journal", methods=["GET"])
def api_journal():
    try:
        filters = {
            "symbol": request.args.get("symbol"),
            "decision": request.args.get("decision"),
            "grade": request.args.get("grade"),
            "trade_type": request.args.get("trade_type"),
            "date": request.args.get("date"),
        }
        limit = int(request.args.get("limit", 250))
        return jsonify({"status": "success", "data": strategy_journal.read(limit=limit, filters=filters)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/analytics/edge-diagnostics", methods=["GET"])
def api_edge_diagnostics():
    try:
        min_sample = int(request.args.get("min_sample", 3))
        limit = int(request.args.get("limit", 12))
        include_backtest = str(request.args.get("include_backtest", "true")).lower() not in {"0", "false", "no"}
        data = build_edge_diagnostics(
            min_sample=max(1, min(min_sample, 100)),
            limit=max(1, min(limit, 50)),
            include_backtest_csv=include_backtest,
        )
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        logger.error(f"Edge diagnostics failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/analytics/strategy-validation", methods=["GET"])
def api_strategy_validation():
    try:
        min_sample = int(request.args.get("min_sample") or os.getenv("VALIDATION_MIN_SAMPLE", 30))
        rolling_window = int(request.args.get("rolling_window") or os.getenv("VALIDATION_ROLLING_WINDOW", 30))
        data = build_validation_report(
            strategy_version=request.args.get("strategy_version"),
            config_version=request.args.get("config_version"),
            symbol_group=request.args.get("symbol_group"),
            min_sample=min_sample,
            rolling_window=rolling_window,
        )
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        logger.error(f"Strategy validation failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/license/status", methods=["GET"])
def api_license_status():
    try:
        result = license_manager.validate(request.args.get("license_key") or os.getenv("LICENSE_KEY"))
        return jsonify({"status": "success", "data": result.to_dict()})
    except Exception as e:
        logger.exception("License status failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/license/activate", methods=["POST"])
@require_permission("licenses")
def api_license_activate():
    try:
        data = request.get_json(silent=True) or {}
        license_key = str(data.get("license_key") or "").strip().upper()
        machine_id = str(data.get("machine_id") or current_machine_identity().get("machine_id") or "").strip()
        if not license_key:
            return jsonify({"status": "error", "message": "license_key is required"}), 400
        if not machine_id:
            return jsonify({"status": "error", "message": "machine_id is required"}), 400
        result = license_manager.activate(license_key, machine_id)
        if result.valid:
            set_env_file_value("LICENSE_KEY", license_key)
            alert_manager.create(
                "License activated",
                f"License {license_key} activated for this machine.",
                severity="success",
                category="system",
                event="license_activated",
                dedupe_key=f"license_activated:{license_key}",
                cooldown_seconds=60,
            )
        return jsonify({"status": "success" if result.valid else "error", "data": result.to_dict(), "message": result.reason}), (200 if result.valid else 400)
    except Exception as e:
        logger.exception("License activation failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/licenses/create", methods=["POST"])
@require_permission("licenses")
@require_role("admin")
def api_license_create():
    try:
        data = request.get_json(silent=True) or {}
        if not data.get("customer_name") or not data.get("email"):
            return jsonify({"status": "error", "message": "customer_name and email are required"}), 400
        created = license_manager.create_license(
            customer_name=data.get("customer_name"),
            email=data.get("email"),
            max_accounts=int(data.get("max_accounts") or 1),
            license_key=data.get("license_key"),
            expires_at=data.get("expires_at"),
        )
        return jsonify({"status": "success", "data": created})
    except Exception as e:
        logger.exception("License create failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/licenses/revoke", methods=["POST"])
@require_permission("licenses")
@require_role("admin")
def api_license_revoke():
    try:
        data = request.get_json(silent=True) or {}
        license_key = str(data.get("license_key") or "").strip().upper()
        if not license_key:
            return jsonify({"status": "error", "message": "license_key is required"}), 400
        updated = license_manager.revoke(license_key)
        if not updated:
            return jsonify({"status": "error", "message": "License not found"}), 404
        alert_manager.create("License revoked", f"License {license_key} was revoked.", severity="warning", category="system", event="license_revoked")
        return jsonify({"status": "success", "data": updated})
    except Exception as e:
        logger.exception("License revoke failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/licenses/extend", methods=["POST"])
@require_permission("licenses")
@require_role("admin")
def api_license_extend():
    try:
        data = request.get_json(silent=True) or {}
        license_key = str(data.get("license_key") or "").strip().upper()
        if not license_key:
            return jsonify({"status": "error", "message": "license_key is required"}), 400
        updated = license_manager.extend(license_key, int(data.get("days") or 365))
        if not updated:
            return jsonify({"status": "error", "message": "License not found"}), 404
        return jsonify({"status": "success", "data": updated})
    except Exception as e:
        logger.exception("License extend failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/licenses/reset-machine", methods=["POST"])
@require_permission("licenses")
@require_role("admin")
def api_license_reset_machine():
    try:
        data = request.get_json(silent=True) or {}
        license_key = str(data.get("license_key") or "").strip().upper()
        if not license_key:
            return jsonify({"status": "error", "message": "license_key is required"}), 400
        updated = license_manager.reset_machine(license_key)
        if not updated:
            return jsonify({"status": "error", "message": "License not found"}), 404
        return jsonify({"status": "success", "data": updated})
    except Exception as e:
        logger.exception("License machine reset failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/licenses", methods=["GET"])
@require_permission("licenses")
@require_role("admin")
def api_licenses():
    try:
        return jsonify({"status": "success", "data": license_manager.list_licenses()})
    except Exception as e:
        logger.exception("License list failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/licenses/<license_key>", methods=["GET"])
@require_permission("licenses")
@require_role("admin")
def api_license_detail(license_key):
    try:
        license_row = license_manager.get(str(license_key).strip().upper())
        if not license_row:
            return jsonify({"status": "error", "message": "License not found"}), 404
        return jsonify({"status": "success", "data": license_row})
    except Exception as e:
        logger.exception("License detail failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/brokers", methods=["GET"])
def api_brokers():
    try:
        return jsonify({
            "status": "success",
            "data": {
                "profiles": broker_manager.list_profiles(),
                "active": broker_manager.get_active_profile(include_secret=False),
            },
        })
    except Exception as e:
        logger.exception("Broker list failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/broker/status", methods=["GET"])
def api_broker_status():
    try:
        active = broker_manager.get_active_profile(include_secret=False) or {}
        connected = False
        symbol_count = 0
        if engine:
            connected = bool(getattr(engine.broker, "is_connected", False))
            symbol_count = len(engine.symbols or [])
        else:
            broker_type = str(active.get("broker_type") or "mt5").lower()
            symbol_key = "BINANCE_TRADING_SYMBOLS" if broker_type == "binance" else "TRADING_SYMBOLS"
            raw_symbols = os.getenv(symbol_key, "")
            symbol_count = len([item for item in raw_symbols.split(",") if item.strip()])
        return jsonify({
            "status": "success",
            "data": {
                "active_broker": active.get("name") or "Default MT5",
                "broker_type": active.get("broker_type") or "mt5",
                "account": active.get("account") or "",
                "server": active.get("server") or "",
                "connected": connected,
                "symbol_count": symbol_count,
            },
        })
    except Exception as e:
        logger.exception("Broker status failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/brokers/add", methods=["POST"])
@require_permission("brokers")
@require_role("admin")
def api_brokers_add():
    try:
        data = request.get_json(silent=True) or {}
        profile = broker_manager.add_profile(data)
        return jsonify({"status": "success", "data": profile})
    except Exception as e:
        logger.exception("Broker add failed")
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/brokers/edit", methods=["POST"])
@require_permission("brokers")
@require_role("admin")
def api_brokers_edit():
    try:
        data = request.get_json(silent=True) or {}
        profile_id = int(data.get("id") or 0)
        if not profile_id:
            return jsonify({"status": "error", "message": "Broker id is required"}), 400
        profile = broker_manager.update_profile(profile_id, data)
        return jsonify({"status": "success", "data": profile})
    except Exception as e:
        logger.exception("Broker edit failed")
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/brokers/disable", methods=["POST"])
@require_permission("brokers")
@require_role("admin")
def api_brokers_disable():
    try:
        data = request.get_json(silent=True) or {}
        profile_id = int(data.get("id") or 0)
        disabled = str(data.get("disabled", True)).lower() not in ["false", "0", "no"]
        if not profile_id:
            return jsonify({"status": "error", "message": "Broker id is required"}), 400
        profile = broker_manager.disable_profile(profile_id, disabled)
        return jsonify({"status": "success", "data": profile})
    except Exception as e:
        logger.exception("Broker disable failed")
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/brokers/active", methods=["POST"])
@require_permission("brokers")
@require_role("admin")
def api_brokers_active():
    try:
        data = request.get_json(silent=True) or {}
        profile_id = int(data.get("id") or 0)
        if not profile_id:
            return jsonify({"status": "error", "message": "Broker id is required"}), 400
        profile = broker_manager.set_active(profile_id)
        return jsonify({"status": "success", "data": profile})
    except Exception as e:
        logger.exception("Broker active switch failed")
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/brokers/test", methods=["POST"])
@require_permission("brokers")
@require_role("admin")
def api_brokers_test():
    try:
        data = request.get_json(silent=True) or {}
        profile_id = int(data.get("id") or 0)
        result = broker_manager.test_connection(profile_id)
        code = 200 if result.get("connected") else 400
        return jsonify({
            "status": "success" if result.get("connected") else "error",
            "message": result.get("message"),
            "data": result,
        }), code
    except Exception as e:
        logger.exception("Broker test failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/analytics/ict-blockers", methods=["GET"])
def api_ict_blockers():
    try:
        limit = int(request.args.get("limit", 50))
        return jsonify({"status": "success", "data": load_dashboard_payload(limit=limit)})
    except Exception as e:
        logger.exception("ICT blocker analytics failed")
        return jsonify({"status": "error", "message": str(e)}), 500


def _backtest_validation_readiness() -> dict:
    enabled = os.getenv("ENFORCE_BACKTEST_VALIDATION", "true").lower() in ["true", "1", "yes"]
    min_sample = int(os.getenv("VALIDATION_MIN_SAMPLE", "30"))
    min_profit_factor = float(os.getenv("MIN_BACKTEST_PROFIT_FACTOR", "1.2"))
    path = os.path.join(BASE_DIR, "data", "trades.csv")
    result = {
        "enabled": enabled,
        "path": path,
        "sample": 0,
        "min_sample": min_sample,
        "expectancy": 0.0,
        "profit_factor": 0.0,
        "min_profit_factor": min_profit_factor,
        "passed": True,
        "blockers": [],
    }
    if not enabled:
        return result
    if not os.path.exists(path):
        result["passed"] = False
        result["blockers"].append(f"Backtest validation file not found: {path}")
        return result
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            trades = list(csv.DictReader(handle))
        metrics = summarize_performance(trades)
        sample = int(metrics.get("total_trades") or 0)
        expectancy = float(metrics.get("expectancy") or 0.0)
        pf = metrics.get("profit_factor")
        pf_value = float("inf") if pf is None and expectancy > 0 else 0.0 if pf is None else float(pf)
        result.update({
            "sample": sample,
            "expectancy": expectancy,
            "profit_factor": pf_value,
        })
        if sample < min_sample:
            result["blockers"].append(f"Backtest sample {sample} < required {min_sample}")
        if expectancy <= 0:
            result["blockers"].append(f"Backtest expectancy {expectancy:.2f} <= 0")
        if pf_value < min_profit_factor:
            result["blockers"].append(f"Backtest profit factor {pf_value:.2f} < {min_profit_factor:.2f}")
        result["passed"] = not result["blockers"]
    except Exception as exc:
        result["passed"] = False
        result["blockers"].append(f"Backtest validation metrics unavailable: {exc}")
    return result


def _build_trade_readiness(status: dict, engine_ref=None) -> dict:
    blockers = []
    warnings = []

    def block(code: str, message: str):
        blockers.append({"code": code, "message": message})

    if not status.get("running"):
        block("engine_stopped", "Bot engine is stopped; scanners and execution are not running.")
    if not status.get("connected"):
        block("broker_disconnected", "Active broker is disconnected.")
    if status.get("market_open") is False:
        block("market_closed", "Market is closed; the bot can only collect signal snapshots.")

    license_status = status.get("license") or {}
    if license_status and license_status.get("trading_allowed") is False:
        block("license_block", license_status.get("reason") or "License blocks live trading.")

    startup_error = getattr(engine_ref, "startup_validation_error", None) if engine_ref else None
    if startup_error:
        block("startup_validation", startup_error)

    validation = _backtest_validation_readiness()
    for reason in validation.get("blockers", []):
        block("backtest_validation", reason)

    try:
        forward = build_validation_report()
        gates = forward.get("gates") or {}
        overall = forward.get("overall") or {}
        if forward.get("status") in {"UNPROVEN", "DEGRADED"}:
            warnings.append({
                "code": "forward_validation",
                "message": (
                    f"Forward validation is {forward.get('status')}: "
                    f"sample={overall.get('sample_size', 0)}, "
                    f"expectancy={float(overall.get('expectancy') or 0):.4f}"
                ),
            })
        if gates.get("degradation_detected"):
            warnings.append({"code": "forward_degradation", "message": "Forward validation detected recent degradation."})
    except Exception as exc:
        warnings.append({"code": "forward_validation_unavailable", "message": str(exc)})

    return {
        "can_trade_now": not blockers,
        "state": "ready" if not blockers else "blocked",
        "blockers": blockers,
        "warnings": warnings,
        "backtest_validation": validation,
    }


@app.route("/api/bot/status", methods=["GET"])
def api_status():
    with _engine_lock:  # CRITICAL: Prevent race condition on status read
        try:
            if engine:
                status = engine.get_status() or {}
                payload = {
                    "running": status.get("running", False),
                    "connected": status.get("connected", False),
                    "market_open": status.get("market_open"),
                    "bot_score": status.get("bot_score"),
                    "balance": status.get("balance"),
                    "equity": status.get("equity"),
                    "free_margin": status.get("free_margin"),
                    "margin_level": status.get("margin_level"),
                    "daily_profit": status.get("daily_profit"),
                    "floating_profit": status.get("floating_profit"),
                    "realized_profit": status.get("realized_profit"),
                    "net_profit": status.get("net_profit"),
                    "floating_drawdown": status.get("floating_drawdown"),
                    "current_open_risk": status.get("current_open_risk"),
                    "max_open_risk": status.get("max_open_risk"),
                    "open_risk_pct": status.get("open_risk_pct"),
                    "max_open_risk_pct": status.get("max_open_risk_pct"),
                    "open_risk_details": status.get("open_risk_details", []),
                    "symbols": status.get("symbols", []),
                    "volume": status.get("volume"),
                    "position_sizing_mode": status.get("position_sizing_mode"),
                    "active_trades": status.get("active_trades", 0),
                    "logic_feed": status.get("logic_feed", []),
                    "scan": status.get("scan", {}),
                    "trade_management": status.get("trade_management", {}),
                    "current_regimes": status.get("current_regimes", {}),
                    "broker": status.get("broker", {}),
                    "license": status.get("license", {}),
                    "live_trading_disabled": status.get("live_trading_disabled", False),
                }
                payload["readiness"] = _build_trade_readiness(payload, engine_ref=engine)
                return jsonify(payload)
            license_status = license_manager.validate().to_dict()
            payload = {
                "running": False,
                "connected": False,
                "market_open": False,
                "bot_score": {
                    "score": 25,
                    "grade": "F",
                    "label": "Engine stopped",
                    "components": [],
                    "summary": "F (25/100) - Engine stopped",
                },
                "equity": None,
                "active_trades": 0,
                "scan": {
                    "interval_seconds": int(os.getenv("SCAN_INTERVAL_SECONDS", 3)),
                    "engine_loop_sleep_seconds": float(os.getenv("ENGINE_LOOP_SLEEP_SECONDS", 3)),
                    "on_new_candle": os.getenv("SCAN_ON_NEW_CANDLE", "false").lower() in ["true", "1", "yes"],
                    "timeframe_minutes": int(os.getenv("SCAN_TIMEFRAME_MINUTES", 5)),
                    "auto_append_market_watch_symbols": os.getenv("AUTO_APPEND_MARKET_WATCH_SYMBOLS", "false").lower() in ["true", "1", "yes"],
                    "last_scan_at": None,
                    "next_scan_at": None,
                    "seconds_until_next_scan": None,
                    "last_signal_count": 0,
                    "duplicate_signal_cooldown_seconds": int(os.getenv("DUPLICATE_SIGNAL_COOLDOWN_SECONDS", 300)),
                    "trade_cooldown_minutes": int(os.getenv("TRADE_COOLDOWN_MINUTES", 1)),
                    "max_trades_per_symbol": int(os.getenv("MAX_TRADES_PER_SYMBOL", 1)),
                    "early_entry_enabled": os.getenv("FEATURE_EARLY_ENTRY", "true").lower() in ["true", "1", "yes"],
                    "early_entry_min_score": float(os.getenv("EARLY_ENTRY_MIN_SCORE", 0.55)),
                },
                "trade_management": {},
                "current_regimes": {},
                "broker": broker_manager.get_active_profile(include_secret=False) or {},
                "license": license_status,
                "live_trading_disabled": not bool(license_status.get("trading_allowed")),
            }
            payload["readiness"] = _build_trade_readiness(payload)
            return jsonify(payload)
        except Exception as e:
            return jsonify({"running": False, "connected": False, "equity": None, "error": str(e)}), 500


@app.route("/api/positions", methods=["GET"])
def api_positions():
    try:
        if engine:
            positions = engine.get_enriched_positions() if hasattr(engine, "get_enriched_positions") else engine.mt5.get_positions()
            return jsonify({"status": "success", "data": positions})
        return jsonify({"status": "success", "data": [], "message": "Engine not running"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/chart-visuals/<symbol>", methods=["GET"])
def api_chart_visuals(symbol):
    try:
        from technical_analysis import build_chart_visuals

        data = build_chart_visuals(symbol)
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/signals", methods=["GET"])
def api_signals():
    try:
        if engine:
            return jsonify({
                "status": "success",
                "data": {
                    "recent": engine.recent_signals,
                    "favorable": engine.favorable_signals,
                },
            })
        return jsonify({"status": "success", "data": {"recent": [], "favorable": []}})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/scanner/debug", methods=["GET"])
def api_scanner_debug():
    try:
        if engine and hasattr(engine, "get_scanner_debug"):
            return jsonify({"status": "success", "data": engine.get_scanner_debug()})
        configured = [
            s.strip().upper()
            for s in os.getenv("TRADING_SYMBOLS", "EURUSD,GBPUSD,USDJPY").split(",")
            if s.strip()
        ]
        return jsonify({
            "status": "success",
            "data": {
                "configured_symbols": configured,
                "active_symbols": [],
                "symbols_loaded": 0,
                "symbols_scanned": 0,
                "last_scan_results": [],
                "symbols_skipped": [],
                "symbol_visibility": {},
                "symbol_mapping": {},
                "rejection_counts": {},
                "message": "Engine not running",
            },
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/kill", methods=["GET", "POST"])
def api_kill():
    try:
        if request.method == "POST" and is_saas_mode() and "panic" not in context_permissions(current_user_context()):
            return jsonify({"status": "error", "message": "Missing permission: panic"}), 403
        if not engine:
            return jsonify({"status": "success", "data": {"all": False}, "message": "Engine not running"})
        if request.method == "GET":
            return jsonify({"status": "success", "data": engine.killed})
        # POST to update
        data = request.json or {}
        symbol = data.get("symbol", "all")
        action = data.get("action")
        if action == "disable":
            engine.killed[symbol] = True
        elif action == "enable":
            engine.killed[symbol] = False
        else:
            return jsonify({"status": "error", "message": "invalid action"}), 400
        return jsonify({"status": "success", "data": engine.killed})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/bot/rules", methods=["GET", "POST"])
def api_rules():
    try:
        if request.method == "POST" and is_saas_mode() and "settings" not in context_permissions(current_user_context()):
            return jsonify({"status": "error", "message": "Missing permission: settings"}), 403
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        if request.method == "GET":
            return jsonify({"status": "success", "data": engine.rule_config})

        data = request.json or {}
        # Allow updating rule toggles live
        for key in ["ema", "volume", "po3"]:
            if key in data:
                engine.rule_config[key] = bool(data[key])
        return jsonify({"status": "success", "data": engine.rule_config})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/sessions", methods=["GET"])
def api_sessions():
    try:
        if engine:
            return jsonify({"status": "success", "data": engine.sessions})
        return jsonify({"status": "error"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/logs", methods=["GET"])
def api_logs():
    try:
        if engine:
            return jsonify({
                "status": "success",
                "data": {
                    "rejections": engine.rejection_logs[-50:],
                    "trades": engine.logger.get_logs(),
                    "signals": engine.signal_history[-50:],
                    "future_trades": engine.future_trades[-50:],
                },
            })
        return jsonify({"status": "success", "data": {"rejections": [], "trades": [], "signals": [], "future_trades": []}})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/stats", methods=["GET"])
def api_stats():
    try:
        if engine:
            stats = engine.logger.get_stats()
            return jsonify({"status": "success", "data": stats})
        return jsonify({"status": "success", "data": {}})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    try:
        if request.method == "POST" and is_saas_mode() and "settings" not in context_permissions(current_user_context()):
            return jsonify({"status": "error", "message": "Missing permission: settings"}), 403
        def as_float(value, default=0.0):
            try:
                return float(value)
            except Exception:
                return default

        def to_percent(value, default=0.0):
            value = as_float(value, default)
            return value * 100 if value <= 1 else value

        def from_percent(value, default=0.0):
            value = as_float(value, default)
            return value / 100 if value > 1 else value

        def from_ui_percent(value, default=0.0):
            return as_float(value, default) / 100.0

        def normalize_fraction(value, default=0.0):
            value = as_float(value, default)
            return value / 100.0 if value >= 1 else value

        load_dotenv(ENV_PATH, override=True)

        if request.method == "GET":
            # return current config from .env
            risk_value = engine.risk_pct if engine else normalize_fraction(os.getenv("RISK_PERCENT", "0.01"), 0.01)
            exposure_value = engine.max_exposure_pct if engine else normalize_fraction(os.getenv("MAX_EXPOSURE_PERCENT", "0.05"), 0.05)
            daily_cap_value = engine.daily_profit_cap if engine else normalize_fraction(os.getenv("DAILY_PROFIT_CAP", "0.02"), 0.02)
            config = {
                "SAAS_MODE": is_saas_mode(),
                "STRATEGY_VERSION": os.getenv("STRATEGY_VERSION", "local-dev"),
                "CONFIG_VERSION": os.getenv("CONFIG_VERSION", ""),
                "SYMBOL_GROUP": os.getenv("SYMBOL_GROUP", ""),
                "VALIDATION_MIN_SAMPLE": int(os.getenv("VALIDATION_MIN_SAMPLE", "30")),
                "VALIDATION_ROLLING_WINDOW": int(os.getenv("VALIDATION_ROLLING_WINDOW", "30")),
                "TRADING_SYMBOLS": os.getenv("TRADING_SYMBOLS", ""),
                "AUTO_APPEND_MARKET_WATCH_SYMBOLS": engine.auto_append_market_watch_symbols if engine else os.getenv("AUTO_APPEND_MARKET_WATCH_SYMBOLS", "false").lower() in ["1", "true", "yes"],
                "TIMEFRAME": os.getenv("TIMEFRAME", "M5"),
                "TRADE_VOLUME": os.getenv("TRADE_VOLUME", "0.01"),
                "POSITION_SIZING_MODE": engine.position_sizing_mode if engine else os.getenv("POSITION_SIZING_MODE", "fixed"),
                "RISK_PERCENT": to_percent(risk_value, 1),
                "MAX_EXPOSURE_PERCENT": to_percent(exposure_value, 5),
                "MAX_DRAWDOWN_PERCENT": to_percent(os.getenv("MAX_DRAWDOWN_PERCENT", "0.05"), 5),
                "MIN_PROFIT_PIPS": os.getenv("MIN_PROFIT_PIPS", "50"),
                "DAILY_PROFIT_CAP": to_percent(daily_cap_value, 2),
                "SCAN_INTERVAL_SECONDS": engine.scan_interval_seconds if engine else int(os.getenv("SCAN_INTERVAL_SECONDS", "3")),
                "ENGINE_LOOP_SLEEP_SECONDS": engine.engine_loop_sleep_seconds if engine else as_float(os.getenv("ENGINE_LOOP_SLEEP_SECONDS", "3"), 3),
                "SCAN_ON_NEW_CANDLE": engine.scan_on_new_candle if engine else os.getenv("SCAN_ON_NEW_CANDLE", "false").lower() in ["1", "true", "yes"],
                "SCAN_TIMEFRAME_MINUTES": engine.scan_timeframe_minutes if engine else int(os.getenv("SCAN_TIMEFRAME_MINUTES", "5")),
                "DUPLICATE_SIGNAL_COOLDOWN_SECONDS": engine.duplicate_signal_cooldown_seconds if engine else int(os.getenv("DUPLICATE_SIGNAL_COOLDOWN_SECONDS", "300")),
                "MIN_EXPECTED_R": engine.min_expected_r if engine else as_float(os.getenv("MIN_EXPECTED_R", "1.2"), 1.2),
                "MIN_EXPECTED_R_SCALP": engine.min_expected_r_scalp if engine else as_float(os.getenv("MIN_EXPECTED_R_SCALP", "0.8"), 0.8),
                "TAKE_PROFIT_R_MULTIPLIER": engine.take_profit_r_multiplier if engine else as_float(os.getenv("TAKE_PROFIT_R_MULTIPLIER", "1.5"), 1.5),
                "TAKE_PROFIT_R_MULTIPLIER_SCALP": engine.take_profit_r_multiplier_scalp if engine else as_float(os.getenv("TAKE_PROFIT_R_MULTIPLIER_SCALP", "1.2"), 1.2),
                "EXECUTION_CONVICTION_THRESHOLD": engine.execution_conviction_threshold if engine else as_float(os.getenv("EXECUTION_CONVICTION_THRESHOLD", "0.45"), 0.45),
                "EXECUTION_SETUP_SCORE_THRESHOLD": engine.execution_setup_score_threshold if engine else as_float(os.getenv("EXECUTION_SETUP_SCORE_THRESHOLD", "0.45"), 0.45),
                "MARKET_EXECUTION_SCORE_THRESHOLD": engine.market_execution_score_threshold if engine else as_float(os.getenv("MARKET_EXECUTION_SCORE_THRESHOLD", "0.60"), 0.60),
                "MARKET_EXECUTION_CONVICTION_THRESHOLD": engine.market_execution_conviction_threshold if engine else as_float(os.getenv("MARKET_EXECUTION_CONVICTION_THRESHOLD", "0.55"), 0.55),
                "MAX_ENTRY_DRIFT_PIPS": engine.max_entry_drift_pips if engine else as_float(os.getenv("MAX_ENTRY_DRIFT_PIPS", "10"), 10),
                "TRAILING_STOP_TRIGGER_PCT": to_percent(engine.trailing_stop_trigger_pct if engine else os.getenv("TRAILING_STOP_TRIGGER_PCT", "0.20"), 20),
                "TRAILING_STOP_LOCK_PIPS": engine.trailing_stop_lock_pips if engine else as_float(os.getenv("TRAILING_STOP_LOCK_PIPS", "0.5"), 0.5),
                "TRAILING_STOP_STEP_PCT": to_percent(engine.trailing_stop_step_pct if engine else os.getenv("TRAILING_STOP_STEP_PCT", "0.15"), 15),
                "TRAILING_STOP_MIN_STEP_PIPS": engine.trailing_stop_min_step_pips if engine else as_float(os.getenv("TRAILING_STOP_MIN_STEP_PIPS", "0.3"), 0.3),
                "FEATURE_TRAILING_TAKE_PROFIT": engine.trailing_tp_enabled if engine else os.getenv("FEATURE_TRAILING_TAKE_PROFIT", "true").lower() in ["1", "true", "yes"],
                "TRAILING_TP_TRIGGER_PCT": to_percent(engine.trailing_tp_trigger_pct if engine else os.getenv("TRAILING_TP_TRIGGER_PCT", "0.8"), 80),
                "TRAILING_TP_EXTENSION_PCT": to_percent(engine.trailing_tp_extension_pct if engine else os.getenv("TRAILING_TP_EXTENSION_PCT", "0.5"), 50),
                "TRAILING_TP_COOLDOWN_SECONDS": engine.trailing_tp_cooldown_seconds if engine else int(os.getenv("TRAILING_TP_COOLDOWN_SECONDS", "300")),
                "FEATURE_PARTIAL_TAKE_PROFIT": engine.partial_tp_enabled if engine else os.getenv("FEATURE_PARTIAL_TAKE_PROFIT", "true").lower() in ["1", "true", "yes"],
                "PARTIAL_TP_TRIGGER_R": engine.partial_tp_trigger_r if engine else as_float(os.getenv("PARTIAL_TP_TRIGGER_R", "0.30"), 0.30),
                "PARTIAL_TP_CLOSE_PCT": to_percent(engine.partial_tp_close_pct if engine else os.getenv("PARTIAL_TP_CLOSE_PCT", "0.5"), 50),
                "FEATURE_REVERSE_PROFIT_EXIT": engine.reverse_profit_exit_enabled if engine else os.getenv("FEATURE_REVERSE_PROFIT_EXIT", "true").lower() in ["1", "true", "yes"],
                "REVERSE_PROFIT_MIN_R": engine.reverse_profit_min_r if engine else as_float(os.getenv("REVERSE_PROFIT_MIN_R", "0.15"), 0.15),
                "REVERSE_PROFIT_GIVEBACK_PCT": to_percent(engine.reverse_profit_giveback_pct if engine else os.getenv("REVERSE_PROFIT_GIVEBACK_PCT", "0.25"), 25),
                "SIGNAL_LOCKOUT_ENABLED": engine.signal_lockout_enabled if engine else os.getenv("SIGNAL_LOCKOUT_ENABLED", "true").lower() in ["1", "true", "yes"],
                "MAX_TRADES_PER_SYMBOL": engine.max_trades_per_symbol if engine else int(os.getenv("MAX_TRADES_PER_SYMBOL", "1")),
                "TRADE_COOLDOWN_MINUTES": engine.trade_cooldown_minutes if engine else int(os.getenv("TRADE_COOLDOWN_MINUTES", "3")),
                "NO_REVENGE_COOLDOWN_SECONDS": engine.no_revenge_cooldown if engine else int(os.getenv("NO_REVENGE_COOLDOWN_SECONDS", str(24*3600))),
                "FEATURE_PROFESSIONAL_EXECUTION_GATE": engine.professional_gate_enabled if engine else os.getenv("FEATURE_PROFESSIONAL_EXECUTION_GATE", "true").lower() in ["1", "true", "yes"],
                "MIN_EXECUTION_GRADE": engine.min_execution_grade if engine else os.getenv("MIN_EXECUTION_GRADE", "B"),
                "ALLOW_C_GRADE_SCALPS": engine.allow_c_scalps if engine else os.getenv("ALLOW_C_GRADE_SCALPS", "false").lower() in ["1", "true", "yes"],
                "MIN_PROFESSIONAL_SETUP_SCORE": engine.min_professional_score if engine else as_float(os.getenv("MIN_PROFESSIONAL_SETUP_SCORE", "0.62"), 0.62),
                "MIN_PROFESSIONAL_CONVICTION": engine.min_professional_conviction if engine else as_float(os.getenv("MIN_PROFESSIONAL_CONVICTION", "0.30"), 0.30),
                "MIN_SESSION_SCORE_FOR_TRADE": engine.min_session_score_for_trade if engine else as_float(os.getenv("MIN_SESSION_SCORE_FOR_TRADE", "0.45"), 0.45),
                "MIN_SESSION_SCORE_FOR_SCALP": engine.min_session_score_for_scalp if engine else as_float(os.getenv("MIN_SESSION_SCORE_FOR_SCALP", "0.65"), 0.65),
                "BLOCK_CONTEXT_WATCH_TRADES": engine.block_context_watch_trades if engine else os.getenv("BLOCK_CONTEXT_WATCH_TRADES", "true").lower() in ["1", "true", "yes"],
                "FEATURE_STRICT_QUALITY_GATE": engine.strict_quality_gate_enabled if engine else os.getenv("FEATURE_STRICT_QUALITY_GATE", "true").lower() in ["1", "true", "yes"],
                "MIN_STRUCTURAL_QUALITY_SCORE": engine.min_structural_quality_score if engine else as_float(os.getenv("MIN_STRUCTURAL_QUALITY_SCORE", "0.55"), 0.55),
                "MIN_DISPLACEMENT_BODY_RATIO": engine.min_displacement_body_ratio if engine else as_float(os.getenv("MIN_DISPLACEMENT_BODY_RATIO", "1.35"), 1.35),
                "MIN_CANDLE_CLOSE_QUALITY": engine.min_candle_close_quality if engine else as_float(os.getenv("MIN_CANDLE_CLOSE_QUALITY", "0.62"), 0.62),
                "MIN_VOLATILITY_QUALITY": engine.min_volatility_quality if engine else as_float(os.getenv("MIN_VOLATILITY_QUALITY", "0.35"), 0.35),
                "MIN_MARKET_QUALITY_SCORE": engine.min_market_quality_score if engine else as_float(os.getenv("MIN_MARKET_QUALITY_SCORE", "0.42"), 0.42),
                "MIN_CONFIDENCE_PERSISTENCE": engine.min_confidence_persistence if engine else int(os.getenv("MIN_CONFIDENCE_PERSISTENCE", "2")),
                "REQUIRE_HTF_AGREEMENT": engine.require_htf_agreement if engine else os.getenv("REQUIRE_HTF_AGREEMENT", "true").lower() in ["1", "true", "yes"],
                "REQUIRE_LIQUIDITY_CONTEXT": engine.require_liquidity_context if engine else os.getenv("REQUIRE_LIQUIDITY_CONTEXT", "true").lower() in ["1", "true", "yes"],
                "FEATURE_EARLY_ENTRY": engine.early_entry_enabled if engine else os.getenv("FEATURE_EARLY_ENTRY", "true").lower() in ["1", "true", "yes"],
                "EARLY_ENTRY_MIN_SCORE": engine.early_entry_min_score if engine else as_float(os.getenv("EARLY_ENTRY_MIN_SCORE", "0.50"), 0.50),
                "EXECUTION_ARCHETYPE_SCORE_THRESHOLD": engine.execution_archetype_score_threshold if engine else as_float(os.getenv("EXECUTION_ARCHETYPE_SCORE_THRESHOLD", "0.58"), 0.58),
                "FEATURE_FALSE_MOVE_DETECTION": engine.false_move_detection_enabled if engine else os.getenv("FEATURE_FALSE_MOVE_DETECTION", "true").lower() in ["1", "true", "yes"],
                "FEATURE_NEWS_MODE": engine.news_mode_enabled if engine else os.getenv("FEATURE_NEWS_MODE", "true").lower() in ["1", "true", "yes"],
                "NEWS_BLOCK_UNSAFE": engine.news_block_unsafe if engine else os.getenv("NEWS_BLOCK_UNSAFE", "true").lower() in ["1", "true", "yes"],
                "NEWS_RISK_MULTIPLIER": to_percent(engine.news_risk_multiplier if engine else os.getenv("NEWS_RISK_MULTIPLIER", "0.35"), 35),
                "NEWS_ALLOW_RETEST_FOLLOW": engine.news_allow_retest_follow if engine else os.getenv("NEWS_ALLOW_RETEST_FOLLOW", "true").lower() in ["1", "true", "yes"],
                "FEATURE_NEWS_LADDER": engine.news_ladder_enabled if engine else os.getenv("FEATURE_NEWS_LADDER", "true").lower() in ["1", "true", "yes"],
                "NEWS_LADDER_MAX_ADDONS": engine.news_ladder_max_addons if engine else int(os.getenv("NEWS_LADDER_MAX_ADDONS", "2")),
                "NEWS_LADDER_MIN_R": engine.news_ladder_min_r if engine else as_float(os.getenv("NEWS_LADDER_MIN_R", "0.55"), 0.55),
                "NEWS_LADDER_VOLUME_PCT": to_percent(engine.news_ladder_volume_pct if engine else os.getenv("NEWS_LADDER_VOLUME_PCT", "0.35"), 35),
                "NEWS_LADDER_COOLDOWN_SECONDS": engine.news_ladder_cooldown_seconds if engine else int(os.getenv("NEWS_LADDER_COOLDOWN_SECONDS", "180")),
                "WAR_ROOM_ENABLED": engine.features.get("war_room", True) if engine else os.getenv("FEATURE_WAR_ROOM", "true").lower() in ["1", "true", "yes"],
                "MT5_ACCOUNT": os.getenv("MT5_ACCOUNT", ""),
                "MT5_SERVER": os.getenv("MT5_SERVER", ""),
                "MT5_PASSWORD_SET": bool(os.getenv("MT5_PASSWORD", "")),
                "TELEGRAM_BOT_TOKEN_SET": bool(os.getenv("TELEGRAM_BOT_TOKEN", "")),
                "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", ""),
                "DISCORD_WEBHOOK_SET": bool(os.getenv("DISCORD_WEBHOOK", "")),
                "RULES": engine.rule_config if engine else {
                    "ema": os.getenv("RULE_EMA", "true").lower() in ["1", "true", "yes"],
                    "volume": os.getenv("RULE_VOLUME", "true").lower() in ["1", "true", "yes"],
                    "po3": os.getenv("RULE_PO3", "true").lower() in ["1", "true", "yes"],
                },
            }
            return jsonify({"status": "success", "data": config})

        # POST to update .env
        data = request.json or {}
        env_path = ENV_PATH

        numeric_fields = {
            "TRADE_VOLUME": (0.0, None),
            "RISK_PERCENT": (0.0, 100.0),
            "MAX_EXPOSURE_PERCENT": (0.0, 100.0),
            "MAX_DRAWDOWN_PERCENT": (0.0, 100.0),
            "MIN_PROFIT_PIPS": (0.0, None),
            "DAILY_PROFIT_CAP": (0.0, 100.0),
            "SCAN_INTERVAL_SECONDS": (1.0, None),
            "ENGINE_LOOP_SLEEP_SECONDS": (0.1, None),
            "SCAN_TIMEFRAME_MINUTES": (1.0, None),
            "DUPLICATE_SIGNAL_COOLDOWN_SECONDS": (0.0, None),
            "MIN_EXPECTED_R": (0.0, None),
            "MIN_EXPECTED_R_SCALP": (0.0, None),
            "TAKE_PROFIT_R_MULTIPLIER": (0.1, None),
            "TAKE_PROFIT_R_MULTIPLIER_SCALP": (0.1, None),
            "EXECUTION_CONVICTION_THRESHOLD": (0.0, 1.0),
            "EXECUTION_SETUP_SCORE_THRESHOLD": (0.0, 1.0),
            "EXECUTION_ARCHETYPE_SCORE_THRESHOLD": (0.0, 1.0),
            "MARKET_EXECUTION_SCORE_THRESHOLD": (0.0, 1.0),
            "MARKET_EXECUTION_CONVICTION_THRESHOLD": (0.0, 1.0),
            "MAX_ENTRY_DRIFT_PIPS": (0.0, None),
            "TRAILING_STOP_TRIGGER_PCT": (0.0, 100.0),
            "TRAILING_STOP_LOCK_PIPS": (0.0, None),
            "TRAILING_STOP_STEP_PCT": (0.0, 100.0),
            "TRAILING_STOP_MIN_STEP_PIPS": (0.0, None),
            "TRAILING_TP_TRIGGER_PCT": (0.0, 100.0),
            "TRAILING_TP_EXTENSION_PCT": (0.0, 500.0),
            "TRAILING_TP_COOLDOWN_SECONDS": (0.0, None),
            "PARTIAL_TP_TRIGGER_R": (0.0, None),
            "PARTIAL_TP_CLOSE_PCT": (0.0, 100.0),
            "REVERSE_PROFIT_MIN_R": (0.0, None),
            "REVERSE_PROFIT_GIVEBACK_PCT": (0.0, 100.0),
            "MAX_TRADES_PER_SYMBOL": (1.0, None),
            "TRADE_COOLDOWN_MINUTES": (0.0, None),
            "NO_REVENGE_COOLDOWN_SECONDS": (0.0, None),
            "MIN_PROFESSIONAL_SETUP_SCORE": (0.0, 1.0),
            "MIN_PROFESSIONAL_CONVICTION": (0.0, 1.0),
            "MIN_SESSION_SCORE_FOR_TRADE": (0.0, 1.0),
            "MIN_SESSION_SCORE_FOR_SCALP": (0.0, 1.0),
            "MIN_STRUCTURAL_QUALITY_SCORE": (0.0, 1.0),
            "MIN_DISPLACEMENT_BODY_RATIO": (0.0, None),
            "MIN_CANDLE_CLOSE_QUALITY": (0.0, 1.0),
            "MIN_VOLATILITY_QUALITY": (0.0, 1.0),
            "MIN_MARKET_QUALITY_SCORE": (0.0, 1.0),
            "MIN_CONFIDENCE_PERSISTENCE": (1.0, 10.0),
            "EARLY_ENTRY_MIN_SCORE": (0.0, 1.0),
            "NEWS_RISK_MULTIPLIER": (0.0, 100.0),
            "NEWS_LADDER_MAX_ADDONS": (0.0, 10.0),
            "NEWS_LADDER_MIN_R": (0.0, None),
            "NEWS_LADDER_VOLUME_PCT": (0.0, 100.0),
            "NEWS_LADDER_COOLDOWN_SECONDS": (0.0, None),
            "VALIDATION_MIN_SAMPLE": (5.0, None),
            "VALIDATION_ROLLING_WINDOW": (5.0, None),
        }
        for field, (minimum, maximum) in numeric_fields.items():
            if field not in data or data[field] in [None, ""]:
                continue
            try:
                value = float(data[field])
            except Exception:
                return jsonify({"status": "error", "message": f"{field} must be numeric"}), 400
            if minimum is not None and value < minimum:
                return jsonify({"status": "error", "message": f"{field} must be at least {minimum:g}"}), 400
            if maximum is not None and value > maximum:
                return jsonify({"status": "error", "message": f"{field} must be no more than {maximum:g}"}), 400

        # read current .env
        env_vars = read_env_file(env_path)

        # update with posted values
        env_vars["TRADING_SYMBOLS"] = data.get("TRADING_SYMBOLS", env_vars.get("TRADING_SYMBOLS", ""))
        env_vars["AUTO_APPEND_MARKET_WATCH_SYMBOLS"] = str(data.get("AUTO_APPEND_MARKET_WATCH_SYMBOLS", env_vars.get("AUTO_APPEND_MARKET_WATCH_SYMBOLS", "false"))).lower()
        env_vars["SAAS_MODE"] = str(data.get("SAAS_MODE", env_vars.get("SAAS_MODE", "false"))).lower()
        env_vars["STRATEGY_VERSION"] = str(data.get("STRATEGY_VERSION", env_vars.get("STRATEGY_VERSION", "local-dev")) or "local-dev")
        env_vars["CONFIG_VERSION"] = str(data.get("CONFIG_VERSION", env_vars.get("CONFIG_VERSION", "")) or "")
        env_vars["SYMBOL_GROUP"] = str(data.get("SYMBOL_GROUP", env_vars.get("SYMBOL_GROUP", "")) or "")
        env_vars["VALIDATION_MIN_SAMPLE"] = str(data.get("VALIDATION_MIN_SAMPLE", env_vars.get("VALIDATION_MIN_SAMPLE", "30")))
        env_vars["VALIDATION_ROLLING_WINDOW"] = str(data.get("VALIDATION_ROLLING_WINDOW", env_vars.get("VALIDATION_ROLLING_WINDOW", "30")))
        env_vars["TIMEFRAME"] = data.get("TIMEFRAME", env_vars.get("TIMEFRAME", "M5"))
        env_vars["TRADE_VOLUME"] = data.get("TRADE_VOLUME", env_vars.get("TRADE_VOLUME", "0.01"))
        env_vars["POSITION_SIZING_MODE"] = str(data.get("POSITION_SIZING_MODE", env_vars.get("POSITION_SIZING_MODE", "fixed"))).lower()
        env_vars["RISK_PERCENT"] = str(from_ui_percent(data.get("RISK_PERCENT", to_percent(normalize_fraction(env_vars.get("RISK_PERCENT", "0.01"), 0.01))), 1.0))
        env_vars["MAX_EXPOSURE_PERCENT"] = str(from_ui_percent(data.get("MAX_EXPOSURE_PERCENT", to_percent(normalize_fraction(env_vars.get("MAX_EXPOSURE_PERCENT", "0.05"), 0.05))), 5.0))
        env_vars["MAX_DRAWDOWN_PERCENT"] = str(from_ui_percent(data.get("MAX_DRAWDOWN_PERCENT", to_percent(normalize_fraction(env_vars.get("MAX_DRAWDOWN_PERCENT", "0.05"), 0.05))), 5.0))
        env_vars["MIN_PROFIT_PIPS"] = data.get("MIN_PROFIT_PIPS", env_vars.get("MIN_PROFIT_PIPS", "50"))
        env_vars["DAILY_PROFIT_CAP"] = str(from_ui_percent(data.get("DAILY_PROFIT_CAP", to_percent(normalize_fraction(env_vars.get("DAILY_PROFIT_CAP", "0.02"), 0.02))), 2.0))
        env_vars["SCAN_INTERVAL_SECONDS"] = str(data.get("SCAN_INTERVAL_SECONDS", env_vars.get("SCAN_INTERVAL_SECONDS", "3")))
        env_vars["ENGINE_LOOP_SLEEP_SECONDS"] = str(data.get("ENGINE_LOOP_SLEEP_SECONDS", env_vars.get("ENGINE_LOOP_SLEEP_SECONDS", "3")))
        env_vars["SCAN_ON_NEW_CANDLE"] = str(data.get("SCAN_ON_NEW_CANDLE", env_vars.get("SCAN_ON_NEW_CANDLE", "false"))).lower()
        env_vars["SCAN_TIMEFRAME_MINUTES"] = str(data.get("SCAN_TIMEFRAME_MINUTES", env_vars.get("SCAN_TIMEFRAME_MINUTES", "5")))
        env_vars["DUPLICATE_SIGNAL_COOLDOWN_SECONDS"] = str(data.get("DUPLICATE_SIGNAL_COOLDOWN_SECONDS", env_vars.get("DUPLICATE_SIGNAL_COOLDOWN_SECONDS", "300")))
        env_vars["MIN_EXPECTED_R"] = str(data.get("MIN_EXPECTED_R", env_vars.get("MIN_EXPECTED_R", "1.2")))
        env_vars["MIN_EXPECTED_R_SCALP"] = str(data.get("MIN_EXPECTED_R_SCALP", env_vars.get("MIN_EXPECTED_R_SCALP", "0.8")))
        env_vars["TAKE_PROFIT_R_MULTIPLIER"] = str(data.get("TAKE_PROFIT_R_MULTIPLIER", env_vars.get("TAKE_PROFIT_R_MULTIPLIER", "1.5")))
        env_vars["TAKE_PROFIT_R_MULTIPLIER_SCALP"] = str(data.get("TAKE_PROFIT_R_MULTIPLIER_SCALP", env_vars.get("TAKE_PROFIT_R_MULTIPLIER_SCALP", "1.2")))
        env_vars["EXECUTION_CONVICTION_THRESHOLD"] = str(data.get("EXECUTION_CONVICTION_THRESHOLD", env_vars.get("EXECUTION_CONVICTION_THRESHOLD", "0.45")))
        env_vars["EXECUTION_SETUP_SCORE_THRESHOLD"] = str(data.get("EXECUTION_SETUP_SCORE_THRESHOLD", env_vars.get("EXECUTION_SETUP_SCORE_THRESHOLD", "0.45")))
        env_vars["MARKET_EXECUTION_SCORE_THRESHOLD"] = str(data.get("MARKET_EXECUTION_SCORE_THRESHOLD", env_vars.get("MARKET_EXECUTION_SCORE_THRESHOLD", "0.60")))
        env_vars["MARKET_EXECUTION_CONVICTION_THRESHOLD"] = str(data.get("MARKET_EXECUTION_CONVICTION_THRESHOLD", env_vars.get("MARKET_EXECUTION_CONVICTION_THRESHOLD", "0.55")))
        env_vars["MAX_ENTRY_DRIFT_PIPS"] = str(data.get("MAX_ENTRY_DRIFT_PIPS", env_vars.get("MAX_ENTRY_DRIFT_PIPS", "10")))
        env_vars["TRAILING_STOP_TRIGGER_PCT"] = str(from_percent(data.get("TRAILING_STOP_TRIGGER_PCT", env_vars.get("TRAILING_STOP_TRIGGER_PCT", "0.20")), 0.20))
        env_vars["TRAILING_STOP_LOCK_PIPS"] = str(data.get("TRAILING_STOP_LOCK_PIPS", env_vars.get("TRAILING_STOP_LOCK_PIPS", "0.5")))
        env_vars["TRAILING_STOP_STEP_PCT"] = str(from_percent(data.get("TRAILING_STOP_STEP_PCT", env_vars.get("TRAILING_STOP_STEP_PCT", "0.15")), 0.15))
        env_vars["TRAILING_STOP_MIN_STEP_PIPS"] = str(data.get("TRAILING_STOP_MIN_STEP_PIPS", env_vars.get("TRAILING_STOP_MIN_STEP_PIPS", "0.3")))
        env_vars["FEATURE_TRAILING_TAKE_PROFIT"] = str(data.get("FEATURE_TRAILING_TAKE_PROFIT", env_vars.get("FEATURE_TRAILING_TAKE_PROFIT", "true"))).lower()
        env_vars["TRAILING_TP_TRIGGER_PCT"] = str(from_percent(data.get("TRAILING_TP_TRIGGER_PCT", env_vars.get("TRAILING_TP_TRIGGER_PCT", "0.8")), 0.8))
        env_vars["TRAILING_TP_EXTENSION_PCT"] = str(from_percent(data.get("TRAILING_TP_EXTENSION_PCT", env_vars.get("TRAILING_TP_EXTENSION_PCT", "0.5")), 0.5))
        env_vars["TRAILING_TP_COOLDOWN_SECONDS"] = str(data.get("TRAILING_TP_COOLDOWN_SECONDS", env_vars.get("TRAILING_TP_COOLDOWN_SECONDS", "300")))
        env_vars["FEATURE_PARTIAL_TAKE_PROFIT"] = str(data.get("FEATURE_PARTIAL_TAKE_PROFIT", env_vars.get("FEATURE_PARTIAL_TAKE_PROFIT", "true"))).lower()
        env_vars["PARTIAL_TP_TRIGGER_R"] = str(data.get("PARTIAL_TP_TRIGGER_R", env_vars.get("PARTIAL_TP_TRIGGER_R", "0.30")))
        env_vars["PARTIAL_TP_CLOSE_PCT"] = str(from_percent(data.get("PARTIAL_TP_CLOSE_PCT", env_vars.get("PARTIAL_TP_CLOSE_PCT", "0.5")), 0.5))
        env_vars["FEATURE_REVERSE_PROFIT_EXIT"] = str(data.get("FEATURE_REVERSE_PROFIT_EXIT", env_vars.get("FEATURE_REVERSE_PROFIT_EXIT", "true"))).lower()
        env_vars["REVERSE_PROFIT_MIN_R"] = str(data.get("REVERSE_PROFIT_MIN_R", env_vars.get("REVERSE_PROFIT_MIN_R", "0.15")))
        env_vars["REVERSE_PROFIT_GIVEBACK_PCT"] = str(from_percent(data.get("REVERSE_PROFIT_GIVEBACK_PCT", env_vars.get("REVERSE_PROFIT_GIVEBACK_PCT", "0.25")), 0.25))
        env_vars["SIGNAL_LOCKOUT_ENABLED"] = str(data.get("SIGNAL_LOCKOUT_ENABLED", env_vars.get("SIGNAL_LOCKOUT_ENABLED", "true"))).lower()
        env_vars["MAX_TRADES_PER_SYMBOL"] = str(data.get("MAX_TRADES_PER_SYMBOL", env_vars.get("MAX_TRADES_PER_SYMBOL", "1")))
        env_vars["TRADE_COOLDOWN_MINUTES"] = str(data.get("TRADE_COOLDOWN_MINUTES", env_vars.get("TRADE_COOLDOWN_MINUTES", "3")))
        env_vars["NO_REVENGE_COOLDOWN_SECONDS"] = str(data.get("NO_REVENGE_COOLDOWN_SECONDS", env_vars.get("NO_REVENGE_COOLDOWN_SECONDS", str(24*3600))))
        env_vars["FEATURE_PROFESSIONAL_EXECUTION_GATE"] = str(data.get("FEATURE_PROFESSIONAL_EXECUTION_GATE", env_vars.get("FEATURE_PROFESSIONAL_EXECUTION_GATE", "true"))).lower()
        env_vars["MIN_EXECUTION_GRADE"] = str(data.get("MIN_EXECUTION_GRADE", env_vars.get("MIN_EXECUTION_GRADE", "B"))).upper()
        env_vars["ALLOW_C_GRADE_SCALPS"] = str(data.get("ALLOW_C_GRADE_SCALPS", env_vars.get("ALLOW_C_GRADE_SCALPS", "false"))).lower()
        env_vars["MIN_PROFESSIONAL_SETUP_SCORE"] = str(data.get("MIN_PROFESSIONAL_SETUP_SCORE", env_vars.get("MIN_PROFESSIONAL_SETUP_SCORE", "0.62")))
        env_vars["MIN_PROFESSIONAL_CONVICTION"] = str(data.get("MIN_PROFESSIONAL_CONVICTION", env_vars.get("MIN_PROFESSIONAL_CONVICTION", "0.30")))
        env_vars["MIN_SESSION_SCORE_FOR_TRADE"] = str(data.get("MIN_SESSION_SCORE_FOR_TRADE", env_vars.get("MIN_SESSION_SCORE_FOR_TRADE", "0.45")))
        env_vars["MIN_SESSION_SCORE_FOR_SCALP"] = str(data.get("MIN_SESSION_SCORE_FOR_SCALP", env_vars.get("MIN_SESSION_SCORE_FOR_SCALP", "0.65")))
        env_vars["BLOCK_CONTEXT_WATCH_TRADES"] = str(data.get("BLOCK_CONTEXT_WATCH_TRADES", env_vars.get("BLOCK_CONTEXT_WATCH_TRADES", "true"))).lower()
        env_vars["FEATURE_STRICT_QUALITY_GATE"] = str(data.get("FEATURE_STRICT_QUALITY_GATE", env_vars.get("FEATURE_STRICT_QUALITY_GATE", "true"))).lower()
        env_vars["MIN_STRUCTURAL_QUALITY_SCORE"] = str(data.get("MIN_STRUCTURAL_QUALITY_SCORE", env_vars.get("MIN_STRUCTURAL_QUALITY_SCORE", "0.55")))
        env_vars["MIN_DISPLACEMENT_BODY_RATIO"] = str(data.get("MIN_DISPLACEMENT_BODY_RATIO", env_vars.get("MIN_DISPLACEMENT_BODY_RATIO", "1.35")))
        env_vars["MIN_CANDLE_CLOSE_QUALITY"] = str(data.get("MIN_CANDLE_CLOSE_QUALITY", env_vars.get("MIN_CANDLE_CLOSE_QUALITY", "0.62")))
        env_vars["MIN_VOLATILITY_QUALITY"] = str(data.get("MIN_VOLATILITY_QUALITY", env_vars.get("MIN_VOLATILITY_QUALITY", "0.35")))
        env_vars["MIN_MARKET_QUALITY_SCORE"] = str(data.get("MIN_MARKET_QUALITY_SCORE", env_vars.get("MIN_MARKET_QUALITY_SCORE", "0.42")))
        env_vars["MIN_CONFIDENCE_PERSISTENCE"] = str(data.get("MIN_CONFIDENCE_PERSISTENCE", env_vars.get("MIN_CONFIDENCE_PERSISTENCE", "2")))
        env_vars["REQUIRE_HTF_AGREEMENT"] = str(data.get("REQUIRE_HTF_AGREEMENT", env_vars.get("REQUIRE_HTF_AGREEMENT", "true"))).lower()
        env_vars["REQUIRE_LIQUIDITY_CONTEXT"] = str(data.get("REQUIRE_LIQUIDITY_CONTEXT", env_vars.get("REQUIRE_LIQUIDITY_CONTEXT", "true"))).lower()
        env_vars["FEATURE_EARLY_ENTRY"] = str(data.get("FEATURE_EARLY_ENTRY", env_vars.get("FEATURE_EARLY_ENTRY", "true"))).lower()
        env_vars["EARLY_ENTRY_MIN_SCORE"] = str(data.get("EARLY_ENTRY_MIN_SCORE", env_vars.get("EARLY_ENTRY_MIN_SCORE", "0.50")))
        env_vars["EXECUTION_ARCHETYPE_SCORE_THRESHOLD"] = str(data.get("EXECUTION_ARCHETYPE_SCORE_THRESHOLD", env_vars.get("EXECUTION_ARCHETYPE_SCORE_THRESHOLD", "0.58")))
        env_vars["FEATURE_FALSE_MOVE_DETECTION"] = str(data.get("FEATURE_FALSE_MOVE_DETECTION", env_vars.get("FEATURE_FALSE_MOVE_DETECTION", "true"))).lower()
        env_vars["FEATURE_NEWS_MODE"] = str(data.get("FEATURE_NEWS_MODE", env_vars.get("FEATURE_NEWS_MODE", "true"))).lower()
        env_vars["NEWS_BLOCK_UNSAFE"] = str(data.get("NEWS_BLOCK_UNSAFE", env_vars.get("NEWS_BLOCK_UNSAFE", "true"))).lower()
        env_vars["NEWS_RISK_MULTIPLIER"] = str(from_percent(data.get("NEWS_RISK_MULTIPLIER", env_vars.get("NEWS_RISK_MULTIPLIER", "0.35")), 0.35))
        env_vars["NEWS_ALLOW_RETEST_FOLLOW"] = str(data.get("NEWS_ALLOW_RETEST_FOLLOW", env_vars.get("NEWS_ALLOW_RETEST_FOLLOW", "true"))).lower()
        env_vars["FEATURE_NEWS_LADDER"] = str(data.get("FEATURE_NEWS_LADDER", env_vars.get("FEATURE_NEWS_LADDER", "true"))).lower()
        env_vars["NEWS_LADDER_MAX_ADDONS"] = str(data.get("NEWS_LADDER_MAX_ADDONS", env_vars.get("NEWS_LADDER_MAX_ADDONS", "2")))
        env_vars["NEWS_LADDER_MIN_R"] = str(data.get("NEWS_LADDER_MIN_R", env_vars.get("NEWS_LADDER_MIN_R", "0.55")))
        env_vars["NEWS_LADDER_VOLUME_PCT"] = str(from_percent(data.get("NEWS_LADDER_VOLUME_PCT", env_vars.get("NEWS_LADDER_VOLUME_PCT", "0.35")), 0.35))
        env_vars["NEWS_LADDER_COOLDOWN_SECONDS"] = str(data.get("NEWS_LADDER_COOLDOWN_SECONDS", env_vars.get("NEWS_LADDER_COOLDOWN_SECONDS", "180")))
        env_vars["FEATURE_WAR_ROOM"] = str(data.get("WAR_ROOM_ENABLED", env_vars.get("FEATURE_WAR_ROOM", "true"))).lower()
        env_vars["MT5_ACCOUNT"] = data.get("MT5_ACCOUNT", env_vars.get("MT5_ACCOUNT", ""))
        env_vars["MT5_SERVER"] = data.get("MT5_SERVER", env_vars.get("MT5_SERVER", ""))
        posted_password = str(data.get("MT5_PASSWORD", "") or "").strip()
        if posted_password and posted_password != "********":
            env_vars["MT5_PASSWORD"] = posted_password
        posted_telegram_token = str(data.get("TELEGRAM_BOT_TOKEN", "") or "").strip()
        if posted_telegram_token and posted_telegram_token != "********":
            env_vars["TELEGRAM_BOT_TOKEN"] = posted_telegram_token
        env_vars["TELEGRAM_CHAT_ID"] = data.get("TELEGRAM_CHAT_ID", env_vars.get("TELEGRAM_CHAT_ID", ""))
        posted_discord_webhook = str(data.get("DISCORD_WEBHOOK", "") or "").strip()
        if posted_discord_webhook and posted_discord_webhook != "********":
            env_vars["DISCORD_WEBHOOK"] = posted_discord_webhook

        # Rule toggles persist to .env
        if "RULES" in data and isinstance(data["RULES"], dict):
            env_vars["RULE_EMA"] = str(data["RULES"].get("ema", True)).lower()
            env_vars["RULE_VOLUME"] = str(data["RULES"].get("volume", True)).lower()
            env_vars["RULE_PO3"] = str(data["RULES"].get("po3", True)).lower()

        # if bot is running, apply new config live
        if engine:
            if "TRADING_SYMBOLS" in data:
                engine.configured_symbols = engine._parse_symbols(data["TRADING_SYMBOLS"])
                engine.symbols = list(engine.configured_symbols)
                try:
                    engine._initialize_symbols_from_market_watch()
                except Exception:
                    logger.exception("Failed to refresh broker symbol mapping after config update")
            if "AUTO_APPEND_MARKET_WATCH_SYMBOLS" in data:
                engine.auto_append_market_watch_symbols = str(data["AUTO_APPEND_MARKET_WATCH_SYMBOLS"]).lower() in ["1", "true", "yes"]
                try:
                    engine._initialize_symbols_from_market_watch()
                except Exception:
                    logger.exception("Failed to refresh broker symbol expansion after config update")
            for key in ["STRATEGY_VERSION", "CONFIG_VERSION", "SYMBOL_GROUP", "VALIDATION_MIN_SAMPLE", "VALIDATION_ROLLING_WINDOW"]:
                if key in env_vars:
                    os.environ[key] = str(env_vars.get(key, ""))
            engine.strategy_context = current_strategy_context(engine.symbols)
            if "TIMEFRAME" in data:
                try:
                    engine.timeframe = int(str(data["TIMEFRAME"]).upper().replace("M", "").replace("H1", "60"))
                except Exception:
                    pass
            if "TRADE_VOLUME" in data:
                try:
                    engine.volume = float(data["TRADE_VOLUME"])
                except Exception:
                    pass
            if "POSITION_SIZING_MODE" in data:
                mode = str(data["POSITION_SIZING_MODE"]).strip().lower()
                if mode in ["fixed", "risk_percent"]:
                    engine.position_sizing_mode = mode
            if "RISK_PERCENT" in data:
                try:
                    engine.risk_pct = from_ui_percent(data["RISK_PERCENT"], 1.0)
                except Exception:
                    pass
            if "MAX_EXPOSURE_PERCENT" in data:
                try:
                    engine.max_exposure_pct = from_ui_percent(data["MAX_EXPOSURE_PERCENT"], 5.0)
                except Exception:
                    pass
            if "MAX_DRAWDOWN_PERCENT" in data:
                try:
                    engine.max_drawdown_pct = from_ui_percent(data["MAX_DRAWDOWN_PERCENT"], 5.0)
                except Exception:
                    pass
            if "SCAN_INTERVAL_SECONDS" in data:
                try:
                    engine.scan_interval_seconds = int(data["SCAN_INTERVAL_SECONDS"])
                except Exception:
                    pass
            if "ENGINE_LOOP_SLEEP_SECONDS" in data:
                try:
                    engine.engine_loop_sleep_seconds = float(data["ENGINE_LOOP_SLEEP_SECONDS"])
                except Exception:
                    pass
            if "SCAN_ON_NEW_CANDLE" in data:
                engine.scan_on_new_candle = bool(data["SCAN_ON_NEW_CANDLE"])
            if "SCAN_TIMEFRAME_MINUTES" in data:
                try:
                    engine.scan_timeframe_minutes = int(data["SCAN_TIMEFRAME_MINUTES"])
                except Exception:
                    pass
            if "DUPLICATE_SIGNAL_COOLDOWN_SECONDS" in data:
                try:
                    engine.duplicate_signal_cooldown_seconds = int(data["DUPLICATE_SIGNAL_COOLDOWN_SECONDS"])
                except Exception:
                    pass
            if "MIN_PROFIT_PIPS" in data:
                try:
                    engine.min_profit_pips = float(data["MIN_PROFIT_PIPS"])
                except Exception:
                    pass
            if "DAILY_PROFIT_CAP" in data:
                try:
                    engine.daily_profit_cap = from_ui_percent(data["DAILY_PROFIT_CAP"], 2.0)
                except Exception:
                    pass
            if "MIN_EXPECTED_R" in data:
                try:
                    engine.min_expected_r = float(data["MIN_EXPECTED_R"])
                except Exception:
                    pass
            if "MIN_EXPECTED_R_SCALP" in data:
                try:
                    engine.min_expected_r_scalp = float(data["MIN_EXPECTED_R_SCALP"])
                except Exception:
                    pass
            if "TAKE_PROFIT_R_MULTIPLIER" in data:
                try:
                    engine.take_profit_r_multiplier = max(0.1, float(data["TAKE_PROFIT_R_MULTIPLIER"]))
                except Exception:
                    pass
            if "TAKE_PROFIT_R_MULTIPLIER_SCALP" in data:
                try:
                    engine.take_profit_r_multiplier_scalp = max(0.1, float(data["TAKE_PROFIT_R_MULTIPLIER_SCALP"]))
                except Exception:
                    pass
            if "EXECUTION_CONVICTION_THRESHOLD" in data:
                try:
                    engine.execution_conviction_threshold = float(data["EXECUTION_CONVICTION_THRESHOLD"])
                except Exception:
                    pass
            if "EXECUTION_SETUP_SCORE_THRESHOLD" in data:
                try:
                    engine.execution_setup_score_threshold = float(data["EXECUTION_SETUP_SCORE_THRESHOLD"])
                except Exception:
                    pass
            if "MARKET_EXECUTION_SCORE_THRESHOLD" in data:
                try:
                    engine.market_execution_score_threshold = float(data["MARKET_EXECUTION_SCORE_THRESHOLD"])
                except Exception:
                    pass
            if "MARKET_EXECUTION_CONVICTION_THRESHOLD" in data:
                try:
                    engine.market_execution_conviction_threshold = float(data["MARKET_EXECUTION_CONVICTION_THRESHOLD"])
                except Exception:
                    pass
            if "MAX_ENTRY_DRIFT_PIPS" in data:
                try:
                    engine.max_entry_drift_pips = float(data["MAX_ENTRY_DRIFT_PIPS"])
                except Exception:
                    pass
            if "TRAILING_STOP_TRIGGER_PCT" in data:
                try:
                    engine.trailing_stop_trigger_pct = from_percent(data["TRAILING_STOP_TRIGGER_PCT"], 0.20)
                except Exception:
                    pass
            if "TRAILING_STOP_LOCK_PIPS" in data:
                try:
                    engine.trailing_stop_lock_pips = float(data["TRAILING_STOP_LOCK_PIPS"])
                except Exception:
                    pass
            if "TRAILING_STOP_STEP_PCT" in data:
                try:
                    engine.trailing_stop_step_pct = from_percent(data["TRAILING_STOP_STEP_PCT"], 0.15)
                except Exception:
                    pass
            if "TRAILING_STOP_MIN_STEP_PIPS" in data:
                try:
                    engine.trailing_stop_min_step_pips = float(data["TRAILING_STOP_MIN_STEP_PIPS"])
                except Exception:
                    pass
            if "FEATURE_TRAILING_TAKE_PROFIT" in data:
                try:
                    engine.trailing_tp_enabled = bool(data["FEATURE_TRAILING_TAKE_PROFIT"])
                except Exception:
                    pass
            if "TRAILING_TP_TRIGGER_PCT" in data:
                try:
                    engine.trailing_tp_trigger_pct = from_percent(data["TRAILING_TP_TRIGGER_PCT"], 0.8)
                except Exception:
                    pass
            if "TRAILING_TP_EXTENSION_PCT" in data:
                try:
                    engine.trailing_tp_extension_pct = from_percent(data["TRAILING_TP_EXTENSION_PCT"], 0.5)
                except Exception:
                    pass
            if "TRAILING_TP_COOLDOWN_SECONDS" in data:
                try:
                    engine.trailing_tp_cooldown_seconds = int(data["TRAILING_TP_COOLDOWN_SECONDS"])
                except Exception:
                    pass
            if "FEATURE_PARTIAL_TAKE_PROFIT" in data:
                engine.partial_tp_enabled = bool(data["FEATURE_PARTIAL_TAKE_PROFIT"])
            if "PARTIAL_TP_TRIGGER_R" in data:
                try:
                    engine.partial_tp_trigger_r = float(data["PARTIAL_TP_TRIGGER_R"])
                except Exception:
                    pass
            if "PARTIAL_TP_CLOSE_PCT" in data:
                try:
                    engine.partial_tp_close_pct = from_percent(data["PARTIAL_TP_CLOSE_PCT"], 0.5)
                except Exception:
                    pass
            if "FEATURE_REVERSE_PROFIT_EXIT" in data:
                engine.reverse_profit_exit_enabled = bool(data["FEATURE_REVERSE_PROFIT_EXIT"])
            if "REVERSE_PROFIT_MIN_R" in data:
                try:
                    engine.reverse_profit_min_r = float(data["REVERSE_PROFIT_MIN_R"])
                except Exception:
                    pass
            if "REVERSE_PROFIT_GIVEBACK_PCT" in data:
                try:
                    engine.reverse_profit_giveback_pct = from_percent(data["REVERSE_PROFIT_GIVEBACK_PCT"], 0.45)
                except Exception:
                    pass
            if "SIGNAL_LOCKOUT_ENABLED" in data:
                try:
                    engine.signal_lockout_enabled = bool(data["SIGNAL_LOCKOUT_ENABLED"])
                except Exception:
                    pass
            if "MAX_TRADES_PER_SYMBOL" in data:
                try:
                    engine.max_trades_per_symbol = int(data["MAX_TRADES_PER_SYMBOL"])
                except Exception:
                    pass
            if "TRADE_COOLDOWN_MINUTES" in data:
                try:
                    engine.trade_cooldown_minutes = int(data["TRADE_COOLDOWN_MINUTES"])
                except Exception:
                    pass
            if "NO_REVENGE_COOLDOWN_SECONDS" in data:
                try:
                    engine.no_revenge_cooldown = int(data["NO_REVENGE_COOLDOWN_SECONDS"])
                except Exception:
                    pass
            if "FEATURE_PROFESSIONAL_EXECUTION_GATE" in data:
                engine.professional_gate_enabled = bool(data["FEATURE_PROFESSIONAL_EXECUTION_GATE"])
            if "MIN_EXECUTION_GRADE" in data:
                engine.min_execution_grade = str(data["MIN_EXECUTION_GRADE"]).upper()
            if "ALLOW_C_GRADE_SCALPS" in data:
                engine.allow_c_scalps = bool(data["ALLOW_C_GRADE_SCALPS"])
            if "MIN_PROFESSIONAL_SETUP_SCORE" in data:
                try:
                    engine.min_professional_score = float(data["MIN_PROFESSIONAL_SETUP_SCORE"])
                except Exception:
                    pass
            if "MIN_PROFESSIONAL_CONVICTION" in data:
                try:
                    engine.min_professional_conviction = float(data["MIN_PROFESSIONAL_CONVICTION"])
                except Exception:
                    pass
            if "MIN_SESSION_SCORE_FOR_TRADE" in data:
                try:
                    engine.min_session_score_for_trade = float(data["MIN_SESSION_SCORE_FOR_TRADE"])
                except Exception:
                    pass
            if "MIN_SESSION_SCORE_FOR_SCALP" in data:
                try:
                    engine.min_session_score_for_scalp = float(data["MIN_SESSION_SCORE_FOR_SCALP"])
                except Exception:
                    pass
            if "BLOCK_CONTEXT_WATCH_TRADES" in data:
                engine.block_context_watch_trades = bool(data["BLOCK_CONTEXT_WATCH_TRADES"])
            if "FEATURE_STRICT_QUALITY_GATE" in data:
                engine.strict_quality_gate_enabled = bool(data["FEATURE_STRICT_QUALITY_GATE"])
            for attr, key, cast in [
                ("min_structural_quality_score", "MIN_STRUCTURAL_QUALITY_SCORE", float),
                ("min_displacement_body_ratio", "MIN_DISPLACEMENT_BODY_RATIO", float),
                ("min_candle_close_quality", "MIN_CANDLE_CLOSE_QUALITY", float),
                ("min_volatility_quality", "MIN_VOLATILITY_QUALITY", float),
                ("min_market_quality_score", "MIN_MARKET_QUALITY_SCORE", float),
                ("min_confidence_persistence", "MIN_CONFIDENCE_PERSISTENCE", int),
            ]:
                if key in data:
                    try:
                        setattr(engine, attr, cast(data[key]))
                    except Exception:
                        pass
            if "REQUIRE_HTF_AGREEMENT" in data:
                engine.require_htf_agreement = bool(data["REQUIRE_HTF_AGREEMENT"])
            if "REQUIRE_LIQUIDITY_CONTEXT" in data:
                engine.require_liquidity_context = bool(data["REQUIRE_LIQUIDITY_CONTEXT"])
            if "FEATURE_EARLY_ENTRY" in data:
                engine.early_entry_enabled = bool(data["FEATURE_EARLY_ENTRY"])
            if "EARLY_ENTRY_MIN_SCORE" in data:
                try:
                    engine.early_entry_min_score = float(data["EARLY_ENTRY_MIN_SCORE"])
                except Exception:
                    pass
            if "EXECUTION_ARCHETYPE_SCORE_THRESHOLD" in data:
                try:
                    engine.execution_archetype_score_threshold = float(data["EXECUTION_ARCHETYPE_SCORE_THRESHOLD"])
                except Exception:
                    pass
            if "FEATURE_FALSE_MOVE_DETECTION" in data:
                engine.false_move_detection_enabled = bool(data["FEATURE_FALSE_MOVE_DETECTION"])
            if "FEATURE_NEWS_MODE" in data:
                engine.news_mode_enabled = bool(data["FEATURE_NEWS_MODE"])
            if "NEWS_BLOCK_UNSAFE" in data:
                engine.news_block_unsafe = bool(data["NEWS_BLOCK_UNSAFE"])
            if "NEWS_RISK_MULTIPLIER" in data:
                try:
                    engine.news_risk_multiplier = from_percent(data["NEWS_RISK_MULTIPLIER"], 0.35)
                except Exception:
                    pass
            if "NEWS_ALLOW_RETEST_FOLLOW" in data:
                engine.news_allow_retest_follow = bool(data["NEWS_ALLOW_RETEST_FOLLOW"])
            if "FEATURE_NEWS_LADDER" in data:
                engine.news_ladder_enabled = bool(data["FEATURE_NEWS_LADDER"])
            if "NEWS_LADDER_MAX_ADDONS" in data:
                try:
                    engine.news_ladder_max_addons = int(data["NEWS_LADDER_MAX_ADDONS"])
                except Exception:
                    pass
            if "NEWS_LADDER_MIN_R" in data:
                try:
                    engine.news_ladder_min_r = float(data["NEWS_LADDER_MIN_R"])
                except Exception:
                    pass
            if "NEWS_LADDER_VOLUME_PCT" in data:
                try:
                    engine.news_ladder_volume_pct = from_percent(data["NEWS_LADDER_VOLUME_PCT"], 0.35)
                except Exception:
                    pass
            if "NEWS_LADDER_COOLDOWN_SECONDS" in data:
                try:
                    engine.news_ladder_cooldown_seconds = int(data["NEWS_LADDER_COOLDOWN_SECONDS"])
                except Exception:
                    pass
            if "WAR_ROOM_ENABLED" in data:
                try:
                    engine.features["war_room"] = bool(data["WAR_ROOM_ENABLED"])
                except Exception:
                    pass
            if "RULES" in data and isinstance(data["RULES"], dict):
                for k in ["ema", "volume", "po3"]:
                    if k in data["RULES"]:
                        engine.rule_config[k] = bool(data["RULES"][k])

        # write back to .env
        with open(env_path, "w") as f:
            f.write("# MT5 Credentials\n")
            f.write(f"MT5_ACCOUNT={env_vars['MT5_ACCOUNT']}\n")
            f.write(f"MT5_PASSWORD={env_vars.get('MT5_PASSWORD', '')}\n")
            f.write(f"MT5_SERVER={env_vars['MT5_SERVER']}\n\n")
            f.write("# Telegram Notifications (Optional)\n")
            f.write(f"TELEGRAM_BOT_TOKEN={env_vars.get('TELEGRAM_BOT_TOKEN', '')}\n")
            f.write(f"TELEGRAM_CHAT_ID={env_vars.get('TELEGRAM_CHAT_ID', '')}\n\n")
            f.write("# Discord Notifications (Optional)\n")
            f.write(f"DISCORD_WEBHOOK={env_vars.get('DISCORD_WEBHOOK', '')}\n\n")
            f.write("# Trading Settings\n")
            f.write(f"SAAS_MODE={env_vars.get('SAAS_MODE', 'false')}\n")
            f.write(f"SAAS_ADMIN_EMAIL={env_vars.get('SAAS_ADMIN_EMAIL', 'admin@nexus.local')}\n")
            f.write(f"SAAS_ADMIN_PASSWORD={env_vars.get('SAAS_ADMIN_PASSWORD', 'change-me-now')}\n")
            f.write(f"SAAS_DEFAULT_TENANT={env_vars.get('SAAS_DEFAULT_TENANT', 'default')}\n")
            f.write(f"LICENSE_KEY={env_vars.get('LICENSE_KEY', '')}\n")
            f.write(f"LICENSE_GRACE_DAYS={env_vars.get('LICENSE_GRACE_DAYS', '7')}\n")
            f.write(f"LICENSE_DB_PATH={env_vars.get('LICENSE_DB_PATH', '')}\n")
            f.write(f"NEXUS_MACHINE_ID={env_vars.get('NEXUS_MACHINE_ID', '')}\n")
            f.write(f"STRATEGY_VERSION={env_vars.get('STRATEGY_VERSION', 'local-dev')}\n")
            f.write(f"CONFIG_VERSION={env_vars.get('CONFIG_VERSION', '')}\n")
            f.write(f"SYMBOL_GROUP={env_vars.get('SYMBOL_GROUP', '')}\n")
            f.write(f"VALIDATION_MIN_SAMPLE={env_vars.get('VALIDATION_MIN_SAMPLE', '30')}\n")
            f.write(f"VALIDATION_ROLLING_WINDOW={env_vars.get('VALIDATION_ROLLING_WINDOW', '30')}\n")
            f.write(f"TRADING_SYMBOLS={env_vars['TRADING_SYMBOLS']}\n")
            f.write(f"AUTO_APPEND_MARKET_WATCH_SYMBOLS={env_vars.get('AUTO_APPEND_MARKET_WATCH_SYMBOLS', 'false')}\n")
            f.write(f"TIMEFRAME={env_vars.get('TIMEFRAME', 'M5')}\n")
            f.write(f"SCAN_INTERVAL_SECONDS={env_vars.get('SCAN_INTERVAL_SECONDS', '3')}\n")
            f.write(f"ENGINE_LOOP_SLEEP_SECONDS={env_vars.get('ENGINE_LOOP_SLEEP_SECONDS', '3')}\n")
            f.write(f"SCAN_ON_NEW_CANDLE={env_vars.get('SCAN_ON_NEW_CANDLE', 'false')}\n")
            f.write(f"SCAN_TIMEFRAME_MINUTES={env_vars.get('SCAN_TIMEFRAME_MINUTES', '5')}\n")
            f.write(f"DUPLICATE_SIGNAL_COOLDOWN_SECONDS={env_vars.get('DUPLICATE_SIGNAL_COOLDOWN_SECONDS', '300')}\n")
            f.write(f"TRADE_VOLUME={env_vars['TRADE_VOLUME']}\n")
            f.write(f"POSITION_SIZING_MODE={env_vars.get('POSITION_SIZING_MODE', 'fixed')}\n")
            f.write(f"RISK_PERCENT={env_vars.get('RISK_PERCENT', '0.01')}\n")
            f.write(f"MAX_EXPOSURE_PERCENT={env_vars.get('MAX_EXPOSURE_PERCENT', '5')}\n")
            f.write(f"MIN_PROFIT_PIPS={env_vars.get('MIN_PROFIT_PIPS', '50')}\n")
            f.write(f"DAILY_PROFIT_CAP={env_vars.get('DAILY_PROFIT_CAP', '0.02')}\n")
            f.write(f"MAX_DRAWDOWN_PERCENT={env_vars.get('MAX_DRAWDOWN_PERCENT', '0.05')}\n")
            f.write(f"MIN_EXPECTED_R={env_vars.get('MIN_EXPECTED_R', '1.2')}\n")
            f.write(f"MIN_EXPECTED_R_SCALP={env_vars.get('MIN_EXPECTED_R_SCALP', '0.8')}\n")
            f.write(f"TAKE_PROFIT_R_MULTIPLIER={env_vars.get('TAKE_PROFIT_R_MULTIPLIER', '1.5')}\n")
            f.write(f"TAKE_PROFIT_R_MULTIPLIER_SCALP={env_vars.get('TAKE_PROFIT_R_MULTIPLIER_SCALP', '1.2')}\n")
            f.write(f"EXECUTION_CONVICTION_THRESHOLD={env_vars.get('EXECUTION_CONVICTION_THRESHOLD', '0.45')}\n")
            f.write(f"EXECUTION_SETUP_SCORE_THRESHOLD={env_vars.get('EXECUTION_SETUP_SCORE_THRESHOLD', '0.45')}\n")
            f.write(f"EXECUTION_ARCHETYPE_SCORE_THRESHOLD={env_vars.get('EXECUTION_ARCHETYPE_SCORE_THRESHOLD', '0.58')}\n")
            f.write(f"MARKET_EXECUTION_SCORE_THRESHOLD={env_vars.get('MARKET_EXECUTION_SCORE_THRESHOLD', '0.60')}\n")
            f.write(f"MARKET_EXECUTION_CONVICTION_THRESHOLD={env_vars.get('MARKET_EXECUTION_CONVICTION_THRESHOLD', '0.55')}\n")
            f.write(f"MAX_ENTRY_DRIFT_PIPS={env_vars.get('MAX_ENTRY_DRIFT_PIPS', '10')}\n")
            f.write(f"MIN_PROFIT_PIPS_FX={env_vars.get('MIN_PROFIT_PIPS_FX', '2')}\n")
            f.write(f"MIN_PROFIT_PIPS_JPY={env_vars.get('MIN_PROFIT_PIPS_JPY', '1')}\n")
            f.write(f"MIN_PROFIT_PIPS_USDJPY={env_vars.get('MIN_PROFIT_PIPS_USDJPY', '1')}\n")
            f.write(f"MIN_PROFIT_PIPS_NZDUSD={env_vars.get('MIN_PROFIT_PIPS_NZDUSD', '2')}\n")
            f.write(f"MIN_PROFIT_PIPS_AUDUSD={env_vars.get('MIN_PROFIT_PIPS_AUDUSD', '2')}\n")
            f.write(f"MIN_PROFIT_PIPS_USDCAD={env_vars.get('MIN_PROFIT_PIPS_USDCAD', '1.5')}\n")
            f.write(f"MIN_PROFIT_PIPS_XAU={env_vars.get('MIN_PROFIT_PIPS_XAU', '30')}\n")
            f.write(f"MIN_PROFIT_PIPS_SCALP={env_vars.get('MIN_PROFIT_PIPS_SCALP', '2')}\n")
            f.write(f"MAX_ENTRY_DRIFT_PIPS_XAU={env_vars.get('MAX_ENTRY_DRIFT_PIPS_XAU', '250')}\n")
            f.write(f"TRAILING_STOP_TRIGGER_PCT={env_vars.get('TRAILING_STOP_TRIGGER_PCT', '0.20')}\n")
            f.write(f"TRAILING_STOP_LOCK_PIPS={env_vars.get('TRAILING_STOP_LOCK_PIPS', '0.5')}\n")
            f.write(f"TRAILING_STOP_STEP_PCT={env_vars.get('TRAILING_STOP_STEP_PCT', '0.15')}\n")
            f.write(f"TRAILING_STOP_MIN_STEP_PIPS={env_vars.get('TRAILING_STOP_MIN_STEP_PIPS', '0.3')}\n")
            f.write(f"FEATURE_TRAILING_TAKE_PROFIT={env_vars.get('FEATURE_TRAILING_TAKE_PROFIT', 'true')}\n")
            f.write(f"TRAILING_TP_TRIGGER_PCT={env_vars.get('TRAILING_TP_TRIGGER_PCT', '0.8')}\n")
            f.write(f"TRAILING_TP_EXTENSION_PCT={env_vars.get('TRAILING_TP_EXTENSION_PCT', '0.5')}\n")
            f.write(f"TRAILING_TP_COOLDOWN_SECONDS={env_vars.get('TRAILING_TP_COOLDOWN_SECONDS', '300')}\n")
            f.write(f"FEATURE_PARTIAL_TAKE_PROFIT={env_vars.get('FEATURE_PARTIAL_TAKE_PROFIT', 'true')}\n")
            f.write(f"PARTIAL_TP_TRIGGER_R={env_vars.get('PARTIAL_TP_TRIGGER_R', '0.30')}\n")
            f.write(f"PARTIAL_TP_CLOSE_PCT={env_vars.get('PARTIAL_TP_CLOSE_PCT', '0.5')}\n")
            f.write(f"FEATURE_REVERSE_PROFIT_EXIT={env_vars.get('FEATURE_REVERSE_PROFIT_EXIT', 'true')}\n")
            f.write(f"REVERSE_PROFIT_MIN_R={env_vars.get('REVERSE_PROFIT_MIN_R', '0.15')}\n")
            f.write(f"REVERSE_PROFIT_GIVEBACK_PCT={env_vars.get('REVERSE_PROFIT_GIVEBACK_PCT', '0.25')}\n")
            f.write(f"REVERSE_PROFIT_CLOSE_PCT={env_vars.get('REVERSE_PROFIT_CLOSE_PCT', '1.0')}\n")
            f.write(f"REVERSE_AFTER_PARTIAL_LOCK_R={env_vars.get('REVERSE_AFTER_PARTIAL_LOCK_R', '0.10')}\n")
            f.write(f"SIGNAL_LOCKOUT_ENABLED={env_vars.get('SIGNAL_LOCKOUT_ENABLED', 'true')}\n")
            f.write(f"MAX_TRADES_PER_SYMBOL={env_vars.get('MAX_TRADES_PER_SYMBOL', '1')}\n")
            f.write(f"TRADE_COOLDOWN_MINUTES={env_vars.get('TRADE_COOLDOWN_MINUTES', '3')}\n")
            f.write(f"NO_REVENGE_COOLDOWN_SECONDS={env_vars.get('NO_REVENGE_COOLDOWN_SECONDS', str(24*3600))}\n")
            f.write(f"FEATURE_PROFESSIONAL_EXECUTION_GATE={env_vars.get('FEATURE_PROFESSIONAL_EXECUTION_GATE', 'true')}\n")
            f.write(f"MIN_EXECUTION_GRADE={env_vars.get('MIN_EXECUTION_GRADE', 'B')}\n")
            f.write(f"ALLOW_C_GRADE_SCALPS={env_vars.get('ALLOW_C_GRADE_SCALPS', 'false')}\n")
            f.write(f"MIN_PROFESSIONAL_SETUP_SCORE={env_vars.get('MIN_PROFESSIONAL_SETUP_SCORE', '0.62')}\n")
            f.write(f"MIN_PROFESSIONAL_CONVICTION={env_vars.get('MIN_PROFESSIONAL_CONVICTION', '0.30')}\n")
            f.write(f"MIN_SESSION_SCORE_FOR_TRADE={env_vars.get('MIN_SESSION_SCORE_FOR_TRADE', '0.45')}\n")
            f.write(f"MIN_SESSION_SCORE_FOR_SCALP={env_vars.get('MIN_SESSION_SCORE_FOR_SCALP', '0.65')}\n")
            f.write(f"BLOCK_CONTEXT_WATCH_TRADES={env_vars.get('BLOCK_CONTEXT_WATCH_TRADES', 'true')}\n")
            f.write(f"FEATURE_STRICT_QUALITY_GATE={env_vars.get('FEATURE_STRICT_QUALITY_GATE', 'true')}\n")
            f.write(f"MIN_STRUCTURAL_QUALITY_SCORE={env_vars.get('MIN_STRUCTURAL_QUALITY_SCORE', '0.55')}\n")
            f.write(f"MIN_DISPLACEMENT_BODY_RATIO={env_vars.get('MIN_DISPLACEMENT_BODY_RATIO', '1.35')}\n")
            f.write(f"MIN_CANDLE_CLOSE_QUALITY={env_vars.get('MIN_CANDLE_CLOSE_QUALITY', '0.62')}\n")
            f.write(f"MIN_VOLATILITY_QUALITY={env_vars.get('MIN_VOLATILITY_QUALITY', '0.35')}\n")
            f.write(f"MIN_MARKET_QUALITY_SCORE={env_vars.get('MIN_MARKET_QUALITY_SCORE', '0.42')}\n")
            f.write(f"MIN_CONFIDENCE_PERSISTENCE={env_vars.get('MIN_CONFIDENCE_PERSISTENCE', '2')}\n")
            f.write(f"REQUIRE_HTF_AGREEMENT={env_vars.get('REQUIRE_HTF_AGREEMENT', 'true')}\n")
            f.write(f"REQUIRE_LIQUIDITY_CONTEXT={env_vars.get('REQUIRE_LIQUIDITY_CONTEXT', 'true')}\n")
            f.write(f"FEATURE_FALSE_MOVE_DETECTION={env_vars.get('FEATURE_FALSE_MOVE_DETECTION', 'true')}\n")
            f.write(f"FEATURE_NEWS_MODE={env_vars.get('FEATURE_NEWS_MODE', 'true')}\n")
            f.write(f"NEWS_BLOCK_UNSAFE={env_vars.get('NEWS_BLOCK_UNSAFE', 'true')}\n")
            f.write(f"NEWS_RISK_MULTIPLIER={env_vars.get('NEWS_RISK_MULTIPLIER', '0.35')}\n")
            f.write(f"NEWS_ALLOW_RETEST_FOLLOW={env_vars.get('NEWS_ALLOW_RETEST_FOLLOW', 'true')}\n")
            f.write(f"FEATURE_NEWS_LADDER={env_vars.get('FEATURE_NEWS_LADDER', 'true')}\n")
            f.write(f"NEWS_LADDER_MAX_ADDONS={env_vars.get('NEWS_LADDER_MAX_ADDONS', '2')}\n")
            f.write(f"NEWS_LADDER_MIN_R={env_vars.get('NEWS_LADDER_MIN_R', '0.55')}\n")
            f.write(f"NEWS_LADDER_VOLUME_PCT={env_vars.get('NEWS_LADDER_VOLUME_PCT', '0.35')}\n")
            f.write(f"NEWS_LADDER_COOLDOWN_SECONDS={env_vars.get('NEWS_LADDER_COOLDOWN_SECONDS', '180')}\n")
            f.write(f"FEATURE_WAR_ROOM={env_vars.get('FEATURE_WAR_ROOM', 'true')}\n\n")
            f.write("# Rule Toggles\n")
            f.write(f"RULE_EMA={env_vars.get('RULE_EMA', 'true')}\n")
            f.write(f"RULE_VOLUME={env_vars.get('RULE_VOLUME', 'true')}\n")
            f.write(f"RULE_PO3={env_vars.get('RULE_PO3', 'true')}\n\n")
            f.write("# Logging\n")
            f.write(f"LOG_LEVEL={env_vars.get('LOG_LEVEL', 'INFO')}\n")

        for key, value in env_vars.items():
            os.environ[key] = str(value)

        hidden_keys = {"MT5_PASSWORD", "TELEGRAM_BOT_TOKEN", "DISCORD_WEBHOOK"}
        changed_keys = sorted(k for k in data.keys() if k not in hidden_keys)
        alert_manager.create(
            "Config changed",
            "Runtime configuration was updated from the dashboard.",
            severity="info",
            category="config",
            event="config_changed",
            metadata={"keys": changed_keys},
        )

        return jsonify({"status": "success", "message": "Config saved"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/pending-orders", methods=["GET"])
def api_pending_orders():
    """Get summary of all pending orders."""
    try:
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        
        summary = engine.pending_order_manager.get_pending_orders_summary()
        return jsonify({"status": "success", "data": summary})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/pending-orders/place", methods=["POST"])
@require_permission("trade")
def api_place_pending_orders():
    """Manually trigger pending order placement for specified symbols."""
    try:
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        
        data = request.json or {}
        symbols = data.get("symbols", engine.symbols)
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",")]
        
        placed = engine.pending_order_manager.scan_and_place_pending_orders(
            symbols,
            volume_func=engine._calculate_volume,
            rr_ratio=engine.take_profit_r_multiplier
        )
        
        return jsonify({
            "status": "success",
            "message": f"Placed {len(placed)} pending orders",
            "data": placed
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/pending-orders/<symbol>", methods=["DELETE"])
@require_permission("trade")
def api_cancel_pending_order(symbol):
    """Cancel a pending order for a specific symbol."""
    try:
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        
        success = engine.pending_order_manager.cancel_pending_order(symbol)
        if success:
            return jsonify({"status": "success", "message": f"Cancelled pending order for {symbol}"})
        else:
            return jsonify({"status": "error", "message": f"No pending order found for {symbol}"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/panic-close", methods=["POST"])
@require_permission("panic")
def api_panic_close():
    """Close all currently open MT5 positions and stop new execution."""
    with _engine_lock:
        try:
            if not engine:
                return jsonify({"status": "error", "message": "Bot not running"}), 400

            engine.killed["all"] = True
            alert_manager.create(
                "Panic close triggered",
                "Global kill switch enabled and panic close requested.",
                severity="danger",
                category="risk",
                event="panic_close_triggered",
            )
            positions = engine.mt5.get_positions() or []
            closed = []
            failed = []

            for position in positions:
                ticket = position.get("ticket")
                symbol = position.get("symbol")
                if not ticket:
                    failed.append({"symbol": symbol, "reason": "Missing position ticket"})
                    continue

                if engine.mt5.close_position(ticket):
                    closed.append({"ticket": ticket, "symbol": symbol})
                    engine.active_trades.pop(symbol, None)
                    try:
                        engine._register_trade_close(symbol)
                    except Exception:
                        pass
                else:
                    failed.append({"ticket": ticket, "symbol": symbol, "reason": "MT5 close failed"})

            return jsonify({
                "status": "success" if not failed else "partial",
                "message": f"Closed {len(closed)} position(s); {len(failed)} failed",
                "data": {
                    "closed": closed,
                    "failed": failed,
                    "kill_switch": engine.killed,
                },
            })
        except Exception as e:
            logger.error(f"Panic close failed: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/endpoints", methods=["GET"])
def api_endpoints():
    """Return known API endpoints for frontend discovery and diagnostics."""
    try:
        endpoints = [
            {"method": "GET", "path": "/api/bot/status", "description": "Bot running and MT5 status"},
            {"method": "POST", "path": "/api/bot/start", "description": "Start bot with option payload"},
            {"method": "POST", "path": "/api/bot/stop", "description": "Stop bot"},
            {"method": "GET", "path": "/api/positions", "description": "Get open positions"},
            {"method": "GET", "path": "/api/chart-visuals/<symbol>", "description": "Get chart trendlines and support/resistance overlays"},
            {"method": "GET", "path": "/api/signals", "description": "Get recent signals"},
            {"method": "GET", "path": "/api/logs", "description": "Get runtime logs and trades"},
            {"method": "GET", "path": "/api/stats", "description": "Get performance stats"},
            {"method": "GET", "path": "/api/risk/status", "description": "Get risk intelligence snapshot"},
            {"method": "GET", "path": "/api/scanner/debug", "description": "Get scanner symbol visibility and per-symbol scan diagnostics"},
            {"method": "GET", "path": "/api/journal", "description": "Get strategy decision journal"},
            {"method": "GET", "path": "/api/analytics/edge-diagnostics", "description": "Get statistical edge diagnostics by strategy component"},
            {"method": "GET", "path": "/api/analytics/strategy-validation", "description": "Get forward-test validation status by strategy/config/symbol group"},
            {"method": "GET", "path": "/api/analytics/ict-blockers", "description": "Get ICT blocker summary and near-miss diagnostics"},
            {"method": "GET", "path": "/api/license/status", "description": "Get current license validation status"},
            {"method": "POST", "path": "/api/license/activate", "description": "Activate and machine-bind a license"},
            {"method": "GET", "path": "/api/users", "description": "Admin list dashboard users"},
            {"method": "POST", "path": "/api/users/create", "description": "Admin create dashboard user"},
            {"method": "POST", "path": "/api/users/update", "description": "Admin update dashboard user role/status"},
            {"method": "GET", "path": "/api/licenses", "description": "Admin list licenses"},
            {"method": "GET", "path": "/api/licenses/<key>", "description": "Admin get license detail"},
            {"method": "POST", "path": "/api/licenses/create", "description": "Admin create license"},
            {"method": "POST", "path": "/api/licenses/revoke", "description": "Admin revoke license"},
            {"method": "POST", "path": "/api/licenses/extend", "description": "Admin extend license"},
            {"method": "POST", "path": "/api/licenses/reset-machine", "description": "Admin reset machine binding"},
            {"method": "GET", "path": "/api/brokers", "description": "List broker profiles and active broker"},
            {"method": "GET", "path": "/api/broker/status", "description": "Get active broker runtime status"},
            {"method": "POST", "path": "/api/brokers/add", "description": "Admin add broker profile"},
            {"method": "POST", "path": "/api/brokers/edit", "description": "Admin edit broker profile"},
            {"method": "POST", "path": "/api/brokers/disable", "description": "Admin disable broker profile"},
            {"method": "POST", "path": "/api/brokers/active", "description": "Admin set active broker"},
            {"method": "POST", "path": "/api/brokers/test", "description": "Admin test broker connection"},
            {"method": "GET", "path": "/api/alerts", "description": "Get dashboard alerts"},
            {"method": "POST", "path": "/api/alerts/clear", "description": "Clear dashboard alerts"},
            {"method": "GET", "path": "/api/tenant/context", "description": "Get SaaS tenant mode context"},
            {"method": "GET", "path": "/api/config", "description": "Get config / env values"},
            {"method": "POST", "path": "/api/config", "description": "Update config file"},
            {"method": "GET", "path": "/api/watchlist", "description": "Get watchlist status"},
            {"method": "GET", "path": "/api/pending-orders", "description": "Get pending orders"},
            {"method": "POST", "path": "/api/pending-orders/place", "description": "Trigger pending order placement"},
            {"method": "DELETE", "path": "/api/pending-orders/<symbol>", "description": "Cancel pending order"},
            {"method": "POST", "path": "/api/panic-close", "description": "Close all open positions and enable global kill switch"},
            {"method": "GET", "path": "/api/kill", "description": "Get kill map"},
            {"method": "POST", "path": "/api/kill", "description": "Set kill/enable for symbol"},
            {"method": "GET", "path": "/api/bot/rules", "description": "Get rule toggles"},
            {"method": "POST", "path": "/api/bot/rules", "description": "Update rule toggles"},
            {"method": "GET", "path": "/api/sessions", "description": "Get trading sessions"},
        ]
        return jsonify({"status": "success", "data": endpoints})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/watchlist", methods=["GET"])
def api_watchlist():
    """Get conditional watchlist status and phase information."""
    try:
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        
        summary = engine.conditional_watchlist_manager.get_watchlist_summary()
        ready = engine.conditional_watchlist_manager.get_ready_for_execution()
        
        return jsonify({
            "status": "success",
            "data": {
                "watchlist": summary,
                "ready_for_execution": ready,
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/watchlist/initialize", methods=["POST"])
@require_permission("trade")
def api_initialize_watchlist():
    """Initialize the conditional watchlist for monitoring."""
    try:
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        
        data = request.json or {}
        symbols = data.get("symbols", engine.symbols)
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",")]
        
        initialized = engine.conditional_watchlist_manager.initialize_watchlist(symbols)
        
        return jsonify({
            "status": "success",
            "message": f"Initialized watchlist for {len(initialized)} symbols",
            "data": initialized
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/watchlist/<symbol>/reset", methods=["POST"])
@require_permission("trade")
def api_reset_watchlist(symbol):
    """Reset a symbol in the watchlist back to Phase 1."""
    try:
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        
        success = engine.conditional_watchlist_manager.reset_symbol(symbol)
        if success:
            return jsonify({"status": "success", "message": f"Reset {symbol} to Phase 1"})
        else:
            return jsonify({"status": "error", "message": f"Symbol {symbol} not in watchlist"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/features", methods=["GET", "POST"])
def api_features():
    """Get or update feature toggles."""
    try:
        if request.method == "POST" and is_saas_mode() and "settings" not in context_permissions(current_user_context()):
            return jsonify({"status": "error", "message": "Missing permission: settings"}), 403
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        
        if request.method == "GET":
            return jsonify({
                "status": "success",
                "data": engine.features
            })
        
        # POST to update features
        data = request.json or {}
        for key in ["pending_orders", "conditional_watchlist"]:
            if key in data:
                engine.features[key] = bool(data[key])
        
        return jsonify({
            "status": "success",
            "message": "Features updated",
            "data": engine.features
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    socketio.run(app, debug=False, host="0.0.0.0", port=5000, use_reloader=False, allow_unsafe_werkzeug=True)
