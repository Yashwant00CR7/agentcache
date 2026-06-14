"""
Flask blueprints for agentmemory-python.

Import and register all blueprints via register_blueprints(app).
"""

from .observations import observations_bp
from .memories import memories_bp
from .search import search_bp
from .graph import graph_bp
from .health import health_bp
from .mcp import mcp_bp
from .migration import migration_bp


def register_blueprints(app):
    """Register all route blueprints on a Flask application instance."""
    app.register_blueprint(observations_bp)
    app.register_blueprint(memories_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(graph_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(mcp_bp)
    app.register_blueprint(migration_bp)
