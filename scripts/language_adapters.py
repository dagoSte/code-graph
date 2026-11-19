"""Built-in language adapters, loaded only from this engine's directory."""
from pathlib import Path
import re
import runpy


CLASSLIKE = {"class", "interface", "trait", "enum"}
LANG_BY_EXT = {}
_FAMILIES = {}


def fam(language):
    return _FAMILIES.get(language, language)


def txt(node):
    return node.text.decode("utf-8", "replace") if node is not None else ""


def last_seg(value):
    return re.split(r"[\\.:/]+", value.strip("\\"))[-1] if value else ""


ADAPTERS = {}
for family, filename in (("python", "language_python.py"), ("php", "language_php.py"),
                         ("js", "language_javascript.py"), ("go", "language_go.py"), ("dart", "language_dart.py"),
                         ("java", "language_java.py"), ("swift", "language_swift.py"), ("sql", "language_sql.py")):
    shared = {name: globals()[name] for name in ("CLASSLIKE", "LANG_BY_EXT", "fam", "txt", "last_seg")}
    adapter = runpy.run_path(str(Path(__file__).with_name(filename)), init_globals=shared)
    ADAPTERS[family] = adapter
    LANG_BY_EXT.update(adapter["EXTENSIONS"])
    _FAMILIES.update({language: family for language in adapter["EXTENSIONS"].values()})
