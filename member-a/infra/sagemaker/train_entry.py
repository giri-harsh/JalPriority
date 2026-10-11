"""SageMaker training entry point for the shortage model (M12, stretch). Reuses ``jp_a.ml.train``.

Run as a SageMaker training job (script mode or a custom image that has ``jp_a`` installed). SageMaker supplies:

* ``SM_CHANNEL_TRAINING``  directory holding ``train.csv`` and ``test.csv`` (the ``a/training/`` prefix)
* ``SM_MODEL_DIR``         where the artefacts must be written (uploaded as ``model.tar.gz``); default ``/opt/ml/model``
* hyper-parameters as command line arguments (``--seed 42``)

It writes ``shortage_xgb_v1.json`` + ``model_meta.json`` into the model directory. When ``--s3-models-uri`` (or
``A_S3_BUCKET``) is given it also copies both files to ``s3://<bucket>/<A_S3_PREFIX>models/`` so that
``A_MODEL_MODE=s3`` can serve them. Locally::

    python infra/sagemaker/train_entry.py --train-dir data/training --model-dir out/models

The data is synthetic; the metrics in ``model_meta.json`` say so.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

DEFAULT_MODEL_DIR = "/opt/ml/model"


def _ensure_jp_a_importable() -> None:
    """Allow ``python infra/sagemaker/train_entry.py`` from a repository checkout without installing jp_a."""
    try:
        import jp_a  # noqa: F401
    except ImportError:
        backend = Path(__file__).resolve().parents[2] / "backend"
        if backend.is_dir():
            sys.path.insert(0, str(backend))


def parse_args(argv: Sequence[str] | None = None, environ: dict[str, str] | None = None) -> argparse.Namespace:
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description="Train the JalPriority shortage model (SageMaker entry point).")
    parser.add_argument("--train-dir", type=Path, default=Path(env.get("SM_CHANNEL_TRAINING", "data/training")),
                        help="directory with train.csv and test.csv")  # fmt: skip
    parser.add_argument("--model-dir", type=Path, default=Path(env.get("SM_MODEL_DIR", DEFAULT_MODEL_DIR)))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--validate", action="store_true", help="also record the train-only holdout check")
    parser.add_argument("--s3-bucket", default=env.get("A_S3_BUCKET") or None)
    parser.add_argument("--s3-prefix", default=env.get("A_S3_PREFIX", "a/"))
    parser.add_argument("--region", default=env.get("A_AWS_REGION", "ap-south-1"))
    args, _unknown = parser.parse_known_args(argv)  # SageMaker may pass extra hyper-parameters
    return args


def upload_artefacts(model_dir: Path, bucket: str, prefix: str, region: str | None, client=None) -> list[str]:
    """Copy the two model files to ``s3://<bucket>/<prefix>models/`` (best effort, returns the S3 keys written)."""
    from jp_a.ml.artefacts import ARTEFACT_FILENAME, META_FILENAME
    from jp_a.repositories.storage_s3 import create_boto3_client, normalise_prefix

    s3 = client if client is not None else create_boto3_client("s3", region)
    keys: list[str] = []
    for name in (ARTEFACT_FILENAME, META_FILENAME):
        key = f"{normalise_prefix(prefix)}models/{name}"
        s3.put_object(Bucket=bucket, Key=key, Body=(model_dir / name).read_bytes(), ContentType="application/json")
        keys.append(key)
    return keys


def main(argv: Sequence[str] | None = None, environ: dict[str, str] | None = None, s3_client=None) -> int:
    _ensure_jp_a_importable()
    from jp_a.ml.train import DEFAULT_VALIDATION_SERVICE_AREAS, train_model

    args = parse_args(argv, environ)
    train_csv, test_csv = args.train_dir / "train.csv", args.train_dir / "test.csv"
    try:
        meta = train_model(
            train_csv,
            test_csv,
            args.model_dir,
            seed=args.seed,
            validation_service_areas=DEFAULT_VALIDATION_SERVICE_AREAS if args.validate else None,
        )
    except (OSError, ValueError) as exc:
        print(f"training failed: {exc}", file=sys.stderr)
        return 1

    summary = {"model_dir": str(args.model_dir), "metrics": meta["metrics"], "uploaded": []}
    if args.s3_bucket:
        try:
            summary["uploaded"] = upload_artefacts(args.model_dir, args.s3_bucket, args.s3_prefix, args.region, s3_client)
        except Exception as exc:  # noqa: BLE001 - the artefacts are already in model_dir; SageMaker uploads them anyway
            print(f"warning: could not copy the artefacts to s3://{args.s3_bucket}: {type(exc).__name__}", file=sys.stderr)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
