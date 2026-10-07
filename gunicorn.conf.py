import os

bind = "0.0.0.0:" + os.getenv("PORT", "10000")
workers = 1
threads = 4           # Handle concurrent API requests
timeout = 120         # Robinhood API calls can be slow
preload_app = False   # Each worker calls create_app() independently
accesslog = "-"       # Log to stdout
errorlog = "-"
loglevel = "info"

