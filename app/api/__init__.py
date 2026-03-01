def register_blueprints(app):
    from app.api.health import bp as health_bp
    from app.api.account import bp as account_bp
    from app.api.positions import bp as positions_bp
    from app.api.orders import bp as orders_bp
    from app.api.portfolio import bp as portfolio_bp
    from app.api.engine_api import bp as engine_bp

    for blueprint in [health_bp, account_bp, positions_bp, orders_bp,
                      portfolio_bp, engine_bp]:
        app.register_blueprint(blueprint, url_prefix="/api")
