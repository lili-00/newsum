from fastapi import FastAPI
from .routers import auth_router, summary_router
from .helpers.summary_helper import lifespan

app = FastAPI(lifespan=lifespan)

app.include_router(auth_router.router)
app.include_router(summary_router.router)


@app.get("/")
async def root():
    return {"message": "This is newsum api"}

