import logging
import sys

from fastapi import FastAPI
from .routers import auth_router, summary_router
from .helpers.summary_helper import lifespan

# --- Add this logging configuration near the start of your app ---
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Create a handler that writes log records to the console (standard output)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO) # Set handler level to INFO

# Get the root logger and add the handler
# You can also get a specific logger like logging.getLogger("my_app_logger")
# if you used that name when getting the logger instance in other files.
root_logger = logging.getLogger()
root_logger.addHandler(console_handler)
root_logger.setLevel(logging.INFO) # Set root logger level to INFO
# --- End of logging configuration ---

app = FastAPI(lifespan=lifespan)

app.include_router(auth_router.router)
app.include_router(summary_router.router)


@app.get("/")
async def root():
    return {"message": "This is newsum api"}

