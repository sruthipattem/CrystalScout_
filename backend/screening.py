"""Model loading, feature generation, and Materials Project screening."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parent
MODEL_PATH = Path(os.getenv("CRYSTALSCOUT_MODEL_PATH", BACKEND_DIR / "models" / "crystalscout_extra_trees.joblib"))
TARGET = "Ionic conductivity (S cm-1)"
STRUCTURE_COLUMNS = ["Space group #", "a", "b", "c", "alpha", "beta", "gamma"]


@lru_cache(maxsize=1)
def get_magpie():
    from matminer.featurizers.composition import ElementProperty

    return ElementProperty.from_preset("magpie")


def model_status() -> dict[str, Any]:
    if not MODEL_PATH.is_file():
        return {"ready": False, "message": "The model has not been trained yet. Run python train_model.py in the backend folder."}
    bundle = load_bundle()
    return {
        "ready": True,
        "model_name": bundle.get("model_name", "Extra Trees Regressor"),
        "feature_count": len(bundle["feature_columns"]),
        "metrics": bundle.get("metrics", {}),
    }


@lru_cache(maxsize=1)
def load_bundle() -> dict[str, Any]:
    if not MODEL_PATH.is_file():
        raise FileNotFoundError("Model file not found. Run python train_model.py first.")
    bundle = joblib.load(MODEL_PATH)
    if not isinstance(bundle, dict) or "pipeline" not in bundle or "feature_columns" not in bundle:
        raise ValueError("Saved model file is not a CrystalScout model bundle.")
    if len(bundle["feature_columns"]) != 139:
        raise ValueError("Saved model feature list does not match the expected 139 features.")
    return bundle


def features_for_structure(structure: Any, space_group_number: int | None = None) -> pd.DataFrame:
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

    composition = structure.composition
    magpie = get_magpie()
    values = magpie.featurize(composition)
    features = dict(zip(magpie.feature_labels(), values))
    lattice = structure.lattice
    if space_group_number is None:
        space_group_number = SpacegroupAnalyzer(structure).get_space_group_number()
    features.update({
        "Space group #": space_group_number,
        "a": lattice.a,
        "b": lattice.b,
        "c": lattice.c,
        "alpha": lattice.alpha,
        "beta": lattice.beta,
        "gamma": lattice.gamma,
    })
    row = pd.DataFrame([features]).reindex(columns=load_bundle()["feature_columns"])
    return row.replace([np.inf, -np.inf], np.nan)


def predict_structure(structure: Any, space_group_number: int | None = None) -> dict[str, Any]:
    bundle = load_bundle()
    row = features_for_structure(structure, space_group_number)
    if row.shape[1] != 139:
        raise ValueError("Could not create the model's complete 139-feature input.")
    predicted_log10 = float(bundle["pipeline"].predict(row)[0])
    # Avoid overflowing JSON with pathological values from malformed inputs.
    predicted_sigma = float(np.power(10.0, np.clip(predicted_log10, -300, 300)))
    return {
        "predicted_log10_conductivity": predicted_log10,
        "predicted_conductivity_s_cm": predicted_sigma,
        "formula": structure.composition.reduced_formula,
        "feature_count": 139,
        "model_name": bundle.get("model_name", "Extra Trees Regressor"),
    }


def get_material_from_mp(material_id: str) -> Any:
    api_key = os.getenv("MP_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Set the MP_API_KEY environment variable to use Materials Project lookups.")
    from mp_api.client import MPRester

    with MPRester(api_key) as mpr:
        docs = mpr.materials.summary.search(
            material_ids=[material_id],
            fields=["material_id", "formula_pretty", "structure", "band_gap", "energy_above_hull", "symmetry"],
        )
    if not docs:
        raise LookupError(f"Materials Project ID '{material_id}' was not found.")
    return docs[0]


def passes_project_filters(doc: Any) -> tuple[bool, str | None]:
    if doc.band_gap is None or doc.band_gap < 0.5:
        return False, "The material does not meet the project band-gap filter (at least 0.5 eV)."
    if doc.energy_above_hull is None or doc.energy_above_hull > 0.10:
        return False, "The material does not meet the project stability filter (energy above hull at most 0.10 eV/atom)."
    return True, None


def rank_materials_from_mp(limit: int = 50) -> dict[str, Any]:
    api_key = os.getenv("MP_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Set the MP_API_KEY environment variable to run a live candidate search.")
    from mp_api.client import MPRester

    with MPRester(api_key) as mpr:
        docs = mpr.materials.summary.search(
            elements=["Li"],
            fields=["material_id", "formula_pretty", "structure", "band_gap", "energy_above_hull", "symmetry"],
            chunk_size=1000,
            num_chunks=None,
        )
    eligible = [doc for doc in docs if passes_project_filters(doc)[0] and doc.structure is not None]
    ranked = []
    failed = 0
    for doc in eligible:
        try:
            prediction = predict_structure(
                doc.structure,
                doc.symmetry.number if doc.symmetry else None,
            )
            ranked.append({
                "material_id": str(doc.material_id),
                "formula": doc.formula_pretty,
                "band_gap_ev": doc.band_gap,
                "energy_above_hull_ev_atom": doc.energy_above_hull,
                **prediction,
            })
        except Exception:
            failed += 1
    ranked.sort(key=lambda item: item["predicted_log10_conductivity"], reverse=True)
    for rank, item in enumerate(ranked, 1):
        item["rank"] = rank
    return {
        "candidates_scanned": len(eligible),
        "predictions_completed": len(ranked),
        "feature_failures": failed,
        "results": ranked[:limit],
        "filters": {"band_gap_ev_min": 0.5, "energy_above_hull_ev_atom_max": 0.10},
    }
