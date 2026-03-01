import os

bind = "0.0.0.0:" + os.getenv("PORT", "10000")
workers = 1           # Single worker — engine thread must be singleton
threads = 4           # Handle concurrent API requests
timeout = 120         # Robinhood API calls can be slow
preload_app = True    # Load app before forking (ensures single engine thread)
accesslog = "-"       # Log to stdout
errorlog = "-"
loglevel = "info"
