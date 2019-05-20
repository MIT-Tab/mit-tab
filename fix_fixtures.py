import json
import random
import sys

LOCATION = sys.argv[1]

text = open(LOCATION).read()

DATAFORM = str(text).strip("'<>() ").replace("'", "\"")
DATA = json.loads(DATAFORM)

CONTENT_TYPE_MODEL = "contenttypes.contenttype"
USED_TIEBREAKERS = set()


def find_content_type(content_type_name):
    for content_type in filter(
            lambda obj: obj.get("model") == CONTENT_TYPE_MODEL, DATA):
        fields = content_type.get("fields", {})
        if fields.get("model", None) == content_type_name and fields.get(
                "app_label", None) == "tab":
            return content_type["pk"]


def fix_type(type_name, ctype_id):
    for model in filter(lambda obj: obj.get("model", None) == type_name, DATA):
        model["fields"]["polymorphic_ctype_id"] = ctype_id
        model["fields"]["tiebreaker"] = random.choice(range(0, 2**16))
        while model["fields"]["tiebreaker"] in USED_TIEBREAKERS:
            model["fields"]["tiebreaker"] = random.choice(range(0, 2**16))
        USED_TIEBREAKERS.add(model["fields"]["tiebreaker"])


fix_type("tab.team", find_content_type("team"))
fix_type("tab.debater", find_content_type("debater"))

with open(LOCATION, "w") as out:
    json.dump(DATA, out)
