# Project Roadmap: Prototype to Production

This document outlines the phased roadmap for scaling our Memory Server project from a prototype to a robust, production-ready product. Each phase builds upon the last, focusing sequentially on Security, Features, Deployment, and Monitoring.

## Phase 1: Stabilization & Hardening (Security First)
**Goal:** Eliminate critical vulnerabilities, patch security gaps, and ensure core functionality is robust against common exploits.

### Tasks
1.  **Security Audit**: Review all authentication logic (`AGENTCACHE_SECRET` usage) across API endpoints to enforce strict access control checks.
2.  **Input Sanitization**: Implement rigorous input sanitization for all data ingress points (API calls, WebSocket messages) to mitigate SQL injection and XSS risks.
3.  **Dependency Hardening**: Audit `requirements.txt` and update all dependencies to their latest secure versions, flagging any version downgrade/upgrade that could introduce a vulnerability.
4.  **Test Baseline**: Run full test suite (`pytest`) and fix every failing test before moving to Phase 2.

## Phase 2: Feature Parity & Depth
**Goal:** Implement all planned features that were marked as incomplete or missing, bringing the system closer to its intended scope.

### Tasks
1.  **Memory Deep Dive**: Fully implement complex memory routines like `folder_graph_build` and ensure atomic behavior for `remember`/`forget` operations.
2.  **Search Optimization**: Optimize the hybrid search component (RRF parameters) and profile database queries to improve retrieval performance.
3.  **MCP Tool Completion**: Complete functionality for all remaining tools listed in `/mcp/tools`.
4.  **UX Polish**: Enhance the dashboard viewer (`/viewer`) to provide clear, actionable feedback on system status (e.g., current embedding model, load metrics).

## Phase 3: Productionization & Deployment
**Goal:** Prepare the application for a stable, scalable production environment.

### Tasks
1.  **Containerization**: Write optimized Dockerfiles for the Flask application and dependencies to ensure environment parity across environments.
2.  **Configuration Decoupling**: Refactor configuration loading to strictly adhere to container-native practices (using ENV variables exclusively) rather than relying on local `.env` file structures for production use.
3.  **Performance Tuning**: Conduct load testing to identify I/O bottlenecks (DB access, network latency). Tune database indices and optimize WebSocket message throughput.
4.  **Scalability Design**: Analyze the statelessness of components to determine optimal strategies for horizontal scaling (e.g., separate API workers vs. WebSocket workers).

## Phase 4: Monitoring & Iteration (Live Operations)
**Goal:** Establish continuous monitoring and a feedback loop for post-launch maintenance.

### Tasks
1.  **Observability Stack**: Implement structured logging across all modules to facilitate easy debugging in production.
2.  **Metrics Collection**: Integrate Prometheus metrics collection for tracking latency, resource utilization, and error rates.
3.  **Anomaly Detection**: Configure alerts for performance degradation or excessive error rates in the data access layer.
4.  **Feedback Mechanism**: Design and implement a simple feedback mechanism for collecting post-launch bug reports and feature requests.