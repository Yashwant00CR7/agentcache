"""
Flask blueprints for agentmemory-python.

Import and register all blueprints via register_blueprints(app).
"""

from .graph import create_graph_bp
from .health import create_health_bp
from .mcp import mcp_bp
from .memories import create_memories_bp
from .migration import migration_bp
from .observations import create_observations_bp, observations_bp
from .search import search_bp


def register_blueprints(app, observation_store=None, search_service=None, kv=None):
    """Register all route blueprints on a Flask application instance."""
    obs_bp = (
        create_observations_bp(observation_store)
        if observation_store
        else observations_bp
    )
    if kv is None and observation_store is not None:
        kv = observation_store.kv
    app.register_blueprint(obs_bp)
    app.register_blueprint(create_memories_bp(kv))
    app.register_blueprint(search_bp)
    app.register_blueprint(create_graph_bp(kv))
    app.register_blueprint(create_health_bp(kv))
    app.register_blueprint(mcp_bp)
    app.register_blueprint(migration_bp)



