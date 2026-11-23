"""Search the source and help files shipped with this MIT-Tab build."""

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from django.conf import settings


ROOTS = {
    "docs": {".md"},
    "settings": {".yaml"},
    "mittab/apps": {".py"},
    "mittab/libs": {".py"},
    "mittab/templates": {".html"},
    "assets/js": {".js"},
}
STOP_WORDS = set(
    "about are can could does for from how into now setting should that the "
    "their this turn use when where which who with would you your".split()
)


@lru_cache(maxsize=2)
def source_files(base_dir):
    """Load only application source, templates, and docs from the local build."""
    base_path = Path(base_dir).resolve()
    files = {}
    for directory, extensions in ROOTS.items():
        for path in sorted((base_path / directory).rglob("*")):
            if path.suffix not in extensions or not path.is_file():
                continue
            relative_path = path.relative_to(base_path)
            if path.resolve() != path or any(part.startswith(".") for part in relative_path.parts):
                continue
            if path.stat().st_size <= 300_000:
                content = path.read_text(encoding="utf-8")
                files[relative_path.as_posix()] = (content.lower(), content.splitlines())
    return files


def source_url(path, line):
    revision = settings.MITTAB_SOURCE_REVISION
    if re.fullmatch(r"[0-9a-f]{40}", revision):
        return f"https://github.com/MIT-Tab/mit-tab/blob/{revision}/{quote(path)}#L{line}"
    return ""


def read_source(path, start_line=1, line_count=40):
    files = source_files(settings.BASE_DIR)
    if (
        not isinstance(path, str) or path not in files
        or type(start_line) is not int or type(line_count) is not int
        or start_line < 1 or not 1 <= line_count <= 70
    ):
        return {"error": "Unknown source file or invalid line range."}
    _, lines = files[path]
    if start_line > len(lines):
        return {"error": "The start line is past the end of the file."}
    excerpt = "\n".join(lines[start_line - 1:start_line - 1 + line_count])
    return {"path": path, "line": start_line, "text": excerpt[:3600]}


def search_source(query):
    if not isinstance(query, str):
        return []
    terms = tuple(dict.fromkeys(
        word for word in re.findall(r"[a-z0-9]+", query[:400].lower())
        if len(word) > 2 and word not in STOP_WORDS
    ))[:8]
    if not terms:
        return []

    matches = []
    for path, (content, lines) in source_files(settings.BASE_DIR).items():
        path_words = path.lower().replace("_", " ").replace("-", " ")
        file_score = sum(term in content for term in terms)
        if not file_score:
            continue
        best_score = 0
        best_line = 1
        for index, line in enumerate(lines, 1):
            line_text = line.lower()
            score = sum(term in line_text for term in terms)
            if score > best_score:
                best_score, best_line = score, index
        score = (
            5 * best_score + 2 * file_score
            + 3 * sum(term in path_words for term in terms)
            + int(path.startswith(("settings/", "docs/", "mittab/templates/")))
        )
        matches.append((-score, path, best_line))

    return [
        read_source(path, max(1, line - 2), 7)
        for _, path, line in sorted(matches)[:5]
    ]
