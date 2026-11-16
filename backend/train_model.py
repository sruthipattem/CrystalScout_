"""Recreate the CrystalScout 2.0 Extra Trees model from OBELiX."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

import joblib
import numpy as np
import pandas as pd
from matminer.featurizers.composition import ElementProperty
from pymatgen.core import Composition
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold, cross_validate
from sklearn.pipeline import make_pipeline

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data" / "obelix"
MODEL_DIR = BASE / "models"
TRAIN_URL = "https://raw.githubusercontent.com/NRC-Mila/OBELiX/main/data/downloads/train.csv"
TEST_URL = "https://raw.githubusercontent.com/NRC-Mila/OBELiX/main/data/downloads/test.csv"
TARGET = "Ionic conductivity (S cm-1)"
FORMULA = "True Composition"
STRUCTURE_FEATURES = ["Space group #", "a", "b", "c", "alpha", "beta", "gamma"]


def download_if_missing(url: str, destination: Path) -> None:
    if destination.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {destination.name} from the OBELiX public dataset...")
    with urlopen(url, timeout=60) as response, destination.open("wb") as output:
        output.write(response.read())


def load_clean(path: Path) -> tuple[pd.DataFrame, pd.Series]:
    frame = pd.read_csv(path)
    raw = frame[TARGET].astype("string").str.strip()
    censored = raw.str.startswith("<", na=False)
    conductivity = pd.to_numeric(raw.mask(censored), errors="coerce")
    valid = conductivity.notna() & np.isfinite(conductivity) & (conductivity > 0)
    clean = frame.loc[valid].copy()
    labels = np.log10(conductivity.loc[valid].astype(float))
    return clean, labels


def make_features(frame: pd.DataFrame, featurizer: ElementProperty) -> tuple[pd.DataFrame, pd.DataFrame]:
    names = featurizer.feature_labels()
    rows: list[list[float]] = []
    valid_indices: list[int] = []
    for index, formula in frame[FORMULA].items():
        try:
            values = featurizer.featurize(Composition(str(formula)))
            if np.isfinite(values).all():
                rows.append(values)
                valid_indices.append(index)
        except Exception:
            continue
    usable_frame = frame.loc[valid_indices].copy()
    composition = pd.DataFrame(rows, index=valid_indices, columns=names)
    structure = usable_frame[STRUCTURE_FEATURES].apply(pd.to_numeric, errors="coerce")
    features = pd.concat([composition, structure], axis=1)
    if features.shape[1] != 139:
        raise RuntimeError(f"Expected 139 features, got {features.shape[1]}.")
    return features.replace([np.inf, -np.inf], np.nan), usable_frame


def composition_key(formula: str) -> str:
    return Composition(str(formula)).reduced_formula


def main() -> None:
    train_path = DATA_DIR / "train.csv"
    test_path = DATA_DIR / "test.csv"
    download_if_missing(TRAIN_URL, train_path)
    download_if_missing(TEST_URL, test_path)

    train_raw = pd.read_csv(train_path)
    test_raw = pd.read_csv(test_path)
    train, y_train = load_clean(train_path)
    test, y_test = load_clean(test_path)
    featurizer = ElementProperty.from_preset("magpie")
    X_train, train_meta = make_features(train, featurizer)
    X_test, test_meta = make_features(test, featurizer)
    y_train = y_train.loc[X_train.index]
    y_test = y_test.loc[X_test.index]

    feature_columns = X_train.columns[X_train.notna().any(axis=0)].tolist()
    X_train = X_train.reindex(columns=feature_columns)
    X_test = X_test.reindex(columns=feature_columns)
    if len(feature_columns) != 139:
        raise RuntimeError(f"Expected 139 usable model inputs, got {len(feature_columns)}.")

    model = make_pipeline(
        SimpleImputer(strategy="median"),
        ExtraTreesRegressor(
            n_estimators=500,
            min_samples_leaf=2,
            max_features=0.7,
            random_state=42,
            n_jobs=-1,
        ),
    )
    model.fit(X_train, y_train)
    test_prediction = model.predict(X_test)

    groups = train_meta.loc[X_train.index, FORMULA].map(composition_key)
    cv = cross_validate(
        model,
        X_train,
        y_train,
        groups=groups,
        cv=GroupKFold(n_splits=5),
        scoring={"mae": "neg_mean_absolute_error", "rmse": "neg_root_mean_squared_error", "r2": "r2"},
        n_jobs=1,
    )
    metrics = {
        "test_mae_log10": float(mean_absolute_error(y_test, test_prediction)),
        "test_rmse_log10": float(np.sqrt(mean_squared_error(y_test, test_prediction))),
        "test_r2": float(r2_score(y_test, test_prediction)),
        "grouped_cv_mae_log10": float(-cv["test_mae"].mean()),
        "grouped_cv_rmse_log10": float(-cv["test_rmse"].mean()),
        "grouped_cv_r2": float(cv["test_r2"].mean()),
        "train_rows_before_censored_removal": int(len(train_raw)),
        "test_rows_before_censored_removal": int(len(test_raw)),
        "train_rows_used": int(len(X_train)),
        "test_rows_used": int(len(X_test)),
        "feature_count": int(len(feature_columns)),
        "target": TARGET,
        "target_transform": "log10",
        "conductivity_unit": "S/cm",
        "data_source": "NRC-Mila/OBELiX, official train/test split",
    }
    bundle = {
        "pipeline": model,
        "feature_columns": feature_columns,
        "target": TARGET,
        "target_transform": "log10",
        "conductivity_unit": "S/cm",
        "model_name": "Extra Trees Regressor",
        "metrics": metrics,
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, MODEL_DIR / "crystalscout_extra_trees.joblib")
    (MODEL_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    print(f"Saved model to: {MODEL_DIR / 'crystalscout_extra_trees.joblib'}")


if __name__ == "__main__":
    main()
