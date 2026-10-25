from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
import json

from buddy.web import (
    api_analyze,
    api_issues,
    api_plan,
    api_chat,
    api_coach,
    api_discover,
    api_git,
    api_tour,
    api_index,
    api_tree,
    provider_status,
    ApiError,
)
from buddy.skill import GitHubError
from buddy.cli import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent / ".env")

app = FastAPI(title="contrib-bud API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(
        status_code=exc.status,
        content={"error": str(exc)},
    )

@app.exception_handler(GitHubError)
async def github_error_handler(request: Request, exc: GitHubError):
    return JSONResponse(
        status_code=400,
        content={"error": str(exc)},
    )

@app.get("/api/status")
def get_status():
    return provider_status()

@app.post("/api/analyze")
def analyze(body: dict):
    return api_analyze(body)

@app.post("/api/issues")
def issues(body: dict):
    return api_issues(body)

@app.post("/api/plan")
def plan(body: dict):
    return api_plan(body)

@app.post("/api/chat")
def chat(body: dict):
    return api_chat(body)

@app.post("/api/tour")
def tour_endpoint(body: dict):
    return api_tour(body)

@app.post("/api/git")
def git_endpoint(body: dict):
    return api_git(body)

@app.post("/api/discover")
def discover_endpoint(body: dict):
    return api_discover(body)

@app.post("/api/coach")
def coach_endpoint(body: dict):
    return api_coach(body)

@app.post("/api/tree")
def tree(body: dict):
    return api_tree(body)

@app.post("/api/index")
def index(body: dict):
    return api_index(body)

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8765, reload=True)
