import json
import subprocess
import zipfile
from pathlib import Path
from docx import Document
from pypdf import PdfReader
from .config import TEXT_LIMIT


def document_text(path: Path, suffix: str):
    if suffix == ".txt":
        raw = path.read_bytes()
        if b"\x00" in raw:
            raise ValueError("TXT должен быть текстовым файлом в UTF-8.")
        text = raw.decode("utf-8-sig")
    elif suffix == ".docx":
        with zipfile.ZipFile(path) as archive:
            if sum(x.file_size for x in archive.infolist()) > 100_000_000:
                raise ValueError("Слишком большой распакованный документ.")
        doc = Document(path)
        parts = []
        for block in doc.iter_inner_content():
            if hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.extend(
                    " | ".join(cell.text for cell in row.cells) for row in block.rows
                )
        text = "\n".join(parts)
    elif suffix == ".pdf":
        if not path.read_bytes()[:8].startswith(b"%PDF-"):
            raise ValueError("Файл не является PDF.")
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("PDF защищён паролем. Загрузите незашифрованный документ.")
        parts = []
        total = 0
        for page in reader.pages:
            part = page.extract_text() or ""
            total += len(part)
            if total > TEXT_LIMIT:
                raise ValueError("В документе больше 200 000 символов.")
            parts.append(part)
        text = "\n".join(parts)
    else:
        raise ValueError("Поддерживаются TXT, DOCX и PDF с текстовым слоем.")
    text = text.strip()
    if not text:
        raise ValueError(
            "Не найден текст. Для сканированного PDF вставьте текст вручную."
        )
    if len(text) > TEXT_LIMIT:
        raise ValueError("В документе больше 200 000 символов.")
    return text


def audio_duration(path: Path, suffix: str):
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        timeout=30,
    )
    if probe.returncode:
        raise ValueError("Не удалось прочитать аудио. Проверьте формат файла.")
    info = json.loads(probe.stdout)
    if not any(s.get("codec_type") == "audio" for s in info["streams"]):
        raise ValueError("В файле нет аудиодорожки.")
    formats = set(info["format"]["format_name"].split(","))
    allowed = {
        ".mp3": {"mp3"},
        ".wav": {"wav"},
        ".m4a": {"mov", "mp4", "m4a"},
        ".webm": {"matroska", "webm"},
    }
    if not formats.intersection(allowed[suffix]):
        raise ValueError("Содержимое аудио не соответствует расширению файла.")
    duration = float(info["format"].get("duration", 0))
    if duration <= 0 and suffix == ".webm":
        # MediaRecorder often omits duration; decode only this local file to measure it.
        result = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(path),
                "-t",
                "7201",
                "-progress",
                "pipe:1",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            timeout=120,
        )
        times = [
            int(line.split("=")[1])
            for line in result.stdout.decode().splitlines()
            if line.startswith("out_time_us=") and line.split("=")[1].isdigit()
        ]
        duration = max(times, default=0) / 1_000_000
    if not 0 < duration <= 7200:
        raise ValueError("Аудио должно длиться от 1 секунды до 120 минут.")
    return duration
