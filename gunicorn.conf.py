import os

bind = "0.0.0.0:" + os.getenv("PORT", "10000")
workers = 1           # Single worker — engine thread must be singleton
threads = 4           # Handle concurrent API requests
timeout = 120         # Robinhood API calls can be slow
preload_app = False   # Each worker must call create_app() so the engine thread starts
accesslog = "-"       # Log to stdout
errorlog = "-"
loglevel = "info"
