def register_blueprints(app):
    from app.api.health import bp as health_bp
    from app.api.account import bp as account_bp
    from app.api.positions import bp as positions_bp
    from app.api.orders import bp as orders_bp
    from app.api.portfolio import bp as portfolio_bp
    from app.api.engine_api import bp as engine_bp
    from app.api.trade import bp as trade_bp
    from app.api.quote import bp as quote_bp
    from app.api.history import bp as history_bp
    from app.api.options import bp as options_bp
    from app.api.auth import bp as auth_bp
    from app.api.snapshot import bp as snapshot_bp
    from app.api.events import bp as events_bp

    for blueprint in [health_bp, account_bp, positions_bp, orders_bp,
                      portfolio_bp, engine_bp, trade_bp, quote_bp,
                      history_bp, options_bp, auth_bp, snapshot_bp,
                      events_bp]:
        app.register_blueprint(blueprint, url_prefix="/api")
