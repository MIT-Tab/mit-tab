import json
import random
import sys

LOCATION = sys.argv[1]

text = open(LOCATION).read()

DATAFORM = str(text).strip("'<>() ").replace("'", "\"")
DATA = json.loads(DATAFORM)

CONTENT_TYPE_MODEL = "contenttypes.contenttype"
USED_TIEBREAKERS = set()


def fix_type(type_name):
    for model in filter(lambda obj: obj.get("model", None) == type_name, DATA):
        del model["fields"]["polymorphic_ctype_id"]

fix_type("tab.team")
fix_type("tab.debater")

with open(LOCATION, "w") as out:
    json.dump(DATA, out)
