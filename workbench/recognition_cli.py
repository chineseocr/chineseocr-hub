"""Run the same multimodal recognition pipeline from a shell, without a web service.

Credentials come from environment variables, not command-line flags or source files.
Inputs use the same bounded image/PDF decoder and temporary private storage as HTTP.
"""

import argparse
import json
import os
import tempfile
from pathlib import Path

from .documents import ingest
from .domain import ProviderInput
from .engine import Worker, enqueue_job
from .recognition import RecognitionInput, prepare_recognition
from .store import Store


def main(argv):
    """Recognize a local file; emit persisted JSON or concatenated text, then clean up."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=["ocr", "documents", "tables"])
    parser.add_argument("file", type=Path)
    parser.add_argument("--format", choices=["json", "text", "markdown"], default="json")
    parser.add_argument("--rotation", type=int, choices=[0, 90, 180, 270], default=0)
    parser.add_argument(
        "--deskew", action="store_true", help="Conservative local projection deskew; no extra OCR model"
    )
    parser.add_argument("--position-mode", choices=["direct", "contextual"], default="direct")
    parser.add_argument("--no-grid-assist", action="store_true", help="Keep only model table structure")
    parser.add_argument("--max-localization-batches", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        url, model = os.environ.get("WORKBENCH_MODEL_BASE_URL"), os.environ.get("WORKBENCH_MODEL_NAME")
        if not url or not model:
            parser.exit(2, "Set WORKBENCH_MODEL_BASE_URL and WORKBENCH_MODEL_NAME first.\n")
        provider = ProviderInput(
            name="Multimodal OCR",
            base_url=url,
            model=model,
            api_key=os.environ.get("WORKBENCH_MODEL_API_KEY", ""),
            request_profile=os.environ.get("WORKBENCH_MODEL_REQUEST_PROFILE", "compatible"),
        )
        with tempfile.TemporaryDirectory(prefix="chineseocr-hub-") as temporary:
            store = Store(temporary)
            with args.file.open("rb") as stream:
                file = ingest(store, args.file.name, stream)
            provider_id = store.put("providers", provider.model_dump())
            request = RecognitionInput(
                task=args.task,
                provider_id=provider_id,
                file_ids=[file["id"]],
                output_format="markdown" if args.format == "markdown" else "text",
                rotation=args.rotation,
                deskew=args.deskew,
                grid_assist=not args.no_grid_assist,
                position_mode=args.position_mode,
                max_localization_batches=args.max_localization_batches,
            )
            key = enqueue_job(store, prepare_recognition(store, request))
            Worker(store).process(key)
            job = store.job(key)
            output = (
                json.dumps(job, ensure_ascii=False, indent=2)
                if args.format == "json"
                else "\n\n".join((unit.get("data") or {}).get("text", "") for unit in job["results"])
            )
            if args.output:
                args.output.write_text(output + "\n", encoding="utf-8")
            else:
                print(output)
            if job["status"] != "succeeded":
                parser.exit(1, "Recognition did not fully succeed. Inspect JSON results.\n")
    except (OSError, ValueError):
        # Invalid provider validation can include secret inputs in its default
        # representation; expose a stable message, never the exception body.
        parser.exit(2, "Invalid input or model configuration; check file, environment and service.\n")
