"""
Flask blueprints for agentmemory-python.

Import and register all blueprints via register_blueprints(app).
"""

from .graph import graph_bp
from .health import health_bp
from .mcp import mcp_bp
from .memories import memories_bp
from .migration import migration_bp
from .observations import create_observations_bp, observations_bp
from .search import search_bp


def register_blueprints(app, observation_store=None, search_service=None):
    """Register all route blueprints on a Flask application instance."""
    obs_bp = (
        create_observations_bp(observation_store)
        if observation_store
        else observations_bp
    )
    app.register_blueprint(obs_bp)
    app.register_blueprint(memories_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(graph_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(mcp_bp)
    app.register_blueprint(migration_bp)
