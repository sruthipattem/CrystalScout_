import io
import os
import re
import sqlite3
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from auth import create_access_token, hash_password, read_access_token, verify_password
from database import initialize_database, list_screenings, save_screening, get_connection
from screening import (
    get_material_from_mp,
    model_status,
    passes_project_filters,
    predict_structure,
    rank_materials_from_mp,
)

app = FastAPI(
    title="CrystalScout API",
    description="CrystalScout screens lithium-containing materials and ranks them by predicted room-temperature ionic conductivity.",
    version="2.0.0",
)

APP_ENV = os.getenv("CRYSTALSCOUT_ENV", "production").strip().lower()
frontend_origins = [
    origin.strip().rstrip("/")
    for origin in os.getenv("FRONTEND_URL", "").split(",")
    if origin.strip()
]
if "*" in frontend_origins:
    raise RuntimeError("FRONTEND_URL must list explicit origins; wildcard CORS is not allowed.")
if APP_ENV == "development":
    frontend_origins.extend([
        "http://127.0.0.1:5500", "http://localhost:5500",
        "http://127.0.0.1:5501", "http://localhost:5501",
        "http://127.0.0.1:5502", "http://localhost:5502",
    ])

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


def current_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)]):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Please log in to use this feature.")
    user_id = read_access_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Your session has expired. Please log in again.")
    return user_id


def validate_password(password: str) -> bool:
    return (
        len(password) >= 8
        and re.search(r"[A-Za-z]", password)
        and re.search(r"[0-9]", password)
        and re.search(r"[^A-Za-z0-9]", password)
    )


def store_prediction(user_id: int, source: str, identifier: str | None, prediction: dict):
    save_screening(user_id, source, identifier, prediction)
    return {
        **prediction,
        "message": "Prediction complete. Use it to prioritize candidates; the model's typical held-out error is about 1.17 log10 units.",
    }


@app.on_event("startup")
def startup():
    initialize_database()


@app.get("/")
def home():
    return {"message": "Welcome to CrystalScout API", "version": app.version}


@app.get("/api/health")
def health():
    try:
        status = model_status()
    except Exception as error:
        status = {"ready": False, "message": f"Model could not be loaded: {error}"}
    return {"status": "ok", "model": status}


@app.post("/api/auth/register")
def register(user: RegisterRequest):
    name = user.name.strip()
    email = user.email.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=400, detail="Enter a valid email")
    if not validate_password(user.password):
        raise HTTPException(status_code=400, detail="Password needs at least 8 characters, a letter, a number, and a special character")

    connection = get_connection()
    try:
        connection.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            (name, email, hash_password(user.password)),
        )
        connection.commit()
        return {"message": "Account created successfully"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    finally:
        connection.close()


@app.post("/api/auth/login")
def login(user: LoginRequest):
    email = user.email.strip().lower()
    connection = get_connection()
    try:
        result = connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    finally:
        connection.close()
    if result is None or not verify_password(user.password, result["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return {
        "message": "Login successful",
        "access_token": create_access_token(result["id"]),
        "token_type": "bearer",
        "name": result["name"],
    }


@app.get("/api/screening/model-status")
def screening_model_status(user_id: Annotated[int, Depends(current_user)]):
    try:
        return model_status()
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"Model unavailable: {error}")


@app.post("/api/screening/predict-cif")
async def predict_cif(
    user_id: Annotated[int, Depends(current_user)],
    file: Annotated[UploadFile, File()],
):
    filename = (file.filename or "").strip()
    if not filename.lower().endswith((".cif", ".mcif")):
        raise HTTPException(status_code=400, detail="Upload a .cif or .mcif crystal structure file.")
    content = await file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CIF files must be 5 MB or smaller.")
    try:
        from pymatgen.core import Structure

        structure = Structure.from_str(content.decode("utf-8"), fmt="cif")
        if "Li" not in structure.composition.elements:
            raise HTTPException(status_code=400, detail="The uploaded crystal structure does not contain lithium.")
        prediction = predict_structure(structure)
        return store_prediction(user_id, "cif_upload", filename, prediction)
    except HTTPException:
        raise
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Could not read or screen this CIF: {error}")


@app.post("/api/screening/predict-material/{material_id}")
def predict_material(material_id: str, user_id: Annotated[int, Depends(current_user)]):
    try:
        doc = get_material_from_mp(material_id)
        passes, reason = passes_project_filters(doc)
        if not passes:
            raise HTTPException(status_code=422, detail=reason)
        prediction = predict_structure(doc.structure, doc.symmetry.number if doc.symmetry else None)
        prediction.update({
            "material_id": str(doc.material_id),
            "band_gap_ev": doc.band_gap,
            "energy_above_hull_ev_atom": doc.energy_above_hull,
        })
        return store_prediction(user_id, "materials_project", str(doc.material_id), prediction)
    except HTTPException:
        raise
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Materials Project lookup failed: {error}")


@app.get("/api/screening/candidates")
def ranked_candidates(
    user_id: Annotated[int, Depends(current_user)],
    limit: int = Query(default=50, ge=1, le=100),
):
    try:
        return rank_materials_from_mp(limit=limit)
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Materials Project ranking failed: {error}")


@app.get("/api/screening/history")
def screening_history(
    user_id: Annotated[int, Depends(current_user)],
    limit: int = Query(default=100, ge=1, le=500),
):
    return {"results": list_screenings(user_id, limit)}
