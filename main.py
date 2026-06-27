from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from telemetry.router import router as telemetry_router
from dotenv import load_dotenv
import os


load_dotenv()

app = FastAPI()

allowed_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]


app.include_router(telemetry_router)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_headers=["*"],
    allow_origins=allowed_origins,
)

