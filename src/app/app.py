from __future__ import annotations

import tempfile
from typing import TYPE_CHECKING, Generator

from flask import Flask, jsonify, render_template, request, Response
from pathlib import Path
from src.known_to_influxdb import line_protocol, write_to_influxDB
from src.unknown_to_known import decode
from src.raw_to_unknown import deserializer
from os import urandom
import threading

if TYPE_CHECKING:
    from werkzeug.datastructures.file_storage import FileStorage

CSV_PARENT_PATH = Path("data/csv")

DATA_FILENAME = "{}.data"
CSV_FILENAME = "{}.csv"
LINE_FILENAME = "{}.line"

app = Flask(__name__)
app.config["tasks"] = {}


@app.route("/")
def home():
    return render_template("index.html")


@app.get("/progress")
def get_progress():
    thread_name = request.args.get("name")

    if thread_name is None:
        return jsonify({"message": "No name parameter provided!"}), 400

    progress = app.config["tasks"].get(thread_name)
    if progress is None:
        return jsonify({"message": "Unknown task name."}), 404

    return jsonify({"progress": progress})


def allowed_file(filename: str) -> bool:
    return filename.lower().endswith(".data")


@app.post("/upload")
def upload_data():
    if len(request.files) == 0:
        return jsonify({"error": "No file uploaded"}), 400

    if not all(
        file.filename and allowed_file(file.filename) for file in request.files.values()
    ):
        return jsonify({"error": "Invalid types uploaded."}), 400

    # Read all file contents upfront to avoid closed file errors
    files_data = [(file.filename, file.read()) for file in request.files.values()]

    conversion_thread = threading.Thread(
        target=convert_files,
        args=(files_data,),
        daemon=True,
        name=urandom(8).hex(),
    )

    app.config["tasks"][conversion_thread.name] = 0
    conversion_thread.start()

    return jsonify({"name": conversion_thread.name})


def convert_files(files):
    for filename, content in files:
        # Create a temporary FileStorage-like object for conversion
        from io import BytesIO
        from werkzeug.datastructures import FileStorage

        file_like = FileStorage(
            stream=BytesIO(content), name=filename, filename=filename
        )

        convert_file(file_like)


def convert_file(file: FileStorage) -> Generator[str]:
    """
    Converts .data following this flow:
        .data (raw) -> .data (unknown) -> .csv (known) -> .line (known)

    Saves the intermediate .csv to CSV_PARENT_PATH

    :param: file The file to convert."""
    assert file.name

    csv_filename = CSV_FILENAME.format(file.name)
    raw_data_filename = DATA_FILENAME.format("raw_" + file.name)
    unknown_data_filename = DATA_FILENAME.format("unknown_" + file.name)
    line_filename = LINE_FILENAME.format(file.name)

    current_thread_name = threading.current_thread().name

    with tempfile.TemporaryDirectory() as temporary_directory:
        parent_path = Path(temporary_directory)
        raw_data_path = parent_path / raw_data_filename
        unknown_data_path = parent_path / unknown_data_filename
        csv_path = CSV_PARENT_PATH / csv_filename
        line_path = CSV_PARENT_PATH / line_filename

        # yield "Saving raw .data file to temp dir..."
        app.config["tasks"][current_thread_name] = 20
        file.save(raw_data_path)  # Save .data file to temp dir
        # yield "Done! Saved .data file to temp dir"

        # CONVERT TO UNKNOWN SAVE TO `unknown_data_path`
        # yield "Deserializing raw .data file to unknown .data file..."
        app.config["tasks"][current_thread_name] = 40
        deserializer.deserialize(
            str(raw_data_path.resolve()), str(unknown_data_path.resolve())
        )

        # yield "Decoding unknown .data file to .csv and saving..."
        app.config["tasks"][current_thread_name] = 60
        decode.make_known(
            str(unknown_data_path.resolve()), str(csv_path.resolve())
        )  # Convert .data file to .csv and save to CSV_PARENT_PATH
        # yield "Done! Decoded .data file"

        # yield "Converting .csv to .line file..."
        app.config["tasks"][current_thread_name] = 80
        line_protocol.convert_to_lineprotocol(
            str(csv_path.resolve()),
            str(line_path.resolve()),
        )  # Convert .csv to .line and save to temp dir
        # yield "Done! Converted .csv file to .line file"

        # yield "Writing .line file to influxDB..."

        app.config["tasks"][current_thread_name] = 100
        try:
            write_to_influxDB.write_to_influxDB(str(line_path.resolve()))
        except FileNotFoundError:
            pass
            # yield "Could not find credential files!"
        # yield "Done! Wrote .line file to influxDB"


if __name__ == "__main__":
    app.run(debug=True)
