"""Restaurant database: LLM-based structuring with schema validation, plus a console CRUD."""
import json
import os
import shutil

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from connoisseur.config import EXTRACTION_MODEL, RESTAURANT_DATA_PATH
from connoisseur.llm import get_chat_model, message_text
from connoisseur.settings import get_settings

FILEPATH = str(RESTAURANT_DATA_PATH)
BACKUP_PATH = FILEPATH + ".bak"
MAX_REPAIR_RETRIES = 5

EXAMPLE_RESTAURANT_PARAGRAPH = (
    "Down in **Santa Monica**, **Mar de Cortez** serves as a **sun-drenched**, **casual taqueria** "
    "specializing in **Baja-style seafood**. With a **4.2/5** rating, it captures the salt-air energy "
    "of the coast through its signature beer-battered snapper tacos and zesty octopus ceviche, making "
    "it a premier spot for open-air dining near the pier. Price range:"
)
EXAMPLE_OUTPUT = """
{
    "name": "Mar de Cortez",
    "location": "Santa Monica",
    "type": "casual taqueria",
    "food_style": "Baja-style seafood",
    "rating": 4.2,
    "price_range": 1,
    "signatures": [
        "beer-battered snapper tacos",
        "zesty octopus ceviche"
    ],
    "vibe": "salt-air energy",
    "environment": "a premier sun-drenched spot for open-air dining near the pier.",
    "shortcomings": []
}
"""


class Restaurant(BaseModel):
    """Schema used to validate every structured restaurant record."""

    name: str
    location: str
    type: str
    food_style: str
    rating: float | None = None
    price_range: int | None = None
    signatures: list[str] = Field(default_factory=list)
    vibe: str | None = None
    environment: str
    shortcomings: list[str] = Field(default_factory=list)


def restaurant_data_structure_prompt_generation(restaurant_paragraph):
    system_msg = (
        "Respond with ONLY the JSON object. Do not include any explanation, preamble, or markdown "
        "formatting. Your entire response must be valid JSON starting with '{' and ending with '}'."
    )

    prompt_txt = f"""
Task:
Your task is to extract structured JSON data from the given restaurant description. The JSON must include exactly these fields: name, location, type, food_style, rating, price_range, signatures, vibe, environment, shortcomings.
Use null for rating or price_range when they are not mentioned, and an empty list for signatures or shortcomings when there are none.

Restaurant description:
{restaurant_paragraph}

Example:
Input Restaurant Description: {EXAMPLE_RESTAURANT_PARAGRAPH}
Output:
{EXAMPLE_OUTPUT}
"""
    return system_msg, prompt_txt


def llm_model(system_msg, prompt_txt, params=None):
    """Call the extraction model through the provider factory (a small Ollama model by default).

    params: optional generation parameters, e.g. {"temperature": 0}
    """
    params = params or {}
    model = get_chat_model(
        model=EXTRACTION_MODEL if get_settings().llm_provider == "ollama" else None,
        temperature=params.get("temperature"),
    )
    response = model.invoke([SystemMessage(content=system_msg), HumanMessage(content=prompt_txt)])
    return message_text(response.content)


def JSON_auto_repair_prompts(response, error_message):
    """Build the prompts that ask the LLM to fix a JSON output that failed validation."""
    system_msg = (
        "You are an expert JSON repair assistant. You excel at finding and fixing errors in JSON data. "
        "Respond with ONLY the corrected JSON object, without any explanation or markdown formatting."
    )

    prompt_txt = f"""
The following JSON data has errors:
{response}

Error message:
{error_message}

Please fix the errors and return the corrected JSON data with the exact and ONLY JSON schema.
Required fields: name, location, type, food_style, rating, price_range, signatures, vibe, environment, shortcomings.
"""
    return system_msg, prompt_txt


def _extract_json_text(text):
    # LLMs sometimes wrap the JSON in markdown fences or add a preamble: keep only the outermost {...}
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return text
    return text[start : end + 1]


def new_data_entry_process(paragraph, itemId):
    """Structure a paragraph with the LLM, validating and auto-repairing the output.

    Returns the validated record (with its itemId), or None if it could not be repaired.
    """
    system_msg, prompt_txt = restaurant_data_structure_prompt_generation(paragraph)
    candidate_json_output = _extract_json_text(llm_model(system_msg, prompt_txt))

    for _ in range(MAX_REPAIR_RETRIES + 1):
        try:
            restaurant_data = Restaurant.model_validate_json(candidate_json_output)
            structured_data = restaurant_data.model_dump()
            structured_data["itemId"] = itemId
            return structured_data
        except ValidationError as e:
            repair_system_msg, repair_prompt = JSON_auto_repair_prompts(candidate_json_output, e.json())
            candidate_json_output = _extract_json_text(llm_model(repair_system_msg, repair_prompt))

    print(f"Could not structure the paragraph for itemId {itemId} after {MAX_REPAIR_RETRIES} repair attempts")
    return None


# Helper functions for the console UI
def load_data(file_path):
    # Return an empty list when the database file does not exist yet
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data, file_path, backup_path=BACKUP_PATH):
    # Keep a copy of the previous version before overwriting the database
    if os.path.exists(file_path):
        shutil.copy(file_path, backup_path)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def show_restaurant_card(res, index):
    print(f"\n--- Record {index} ---")
    for key, value in res.items():
        print(f"{key}: {value}")


def read_record_index(data):
    # Ask the user for a record index; return it as an int, or None when it is not valid.
    # str() because the input may not be a string (the unit tests mock input() with an int)
    record_index = str(input("Enter the record index: ")).strip()
    if record_index.isdigit() and int(record_index) < len(data):
        return int(record_index)
    print("Invalid index.")
    return None


def parse_field_value(key, raw_value):
    # Convert what the user typed to the type expected by the schema
    if key == "rating":
        return float(raw_value)
    if key == "price_range":
        return int(raw_value)
    if key in ("signatures", "shortcomings"):
        return [item.strip() for item in raw_value.split(",") if item.strip()]
    return raw_value


def manage_restaurants(file_path, backup_path):
    while True:
        data = load_data(file_path)
        print(f"\n🏨 RESTAURANT DATABASE | Records: {len(data)}")
        print("1. Browse All (Names)")
        print("2. View Detailed Record")
        print("3. Add New Restaurant")
        print("4. Edit Restaurant Info")
        print("5. Delete Restaurant")
        print("6. Exit")

        choice = input("\nAction: ")

        if choice == "1":
            print("\n--- Current Listings ---")
            for index, record in enumerate(data):
                print(f"{index}: {record.get('name', 'N/A')}")

        elif choice == "2":
            record_index = read_record_index(data)
            if record_index is not None:
                show_restaurant_card(data[record_index], record_index)

        elif choice in ["3", "4", "5"]:
            # Write operations need an explicit confirmation
            print("\n❗ SECURITY WARNING: You are entering write-mode.")
            print("Changes will be saved to the database immediately.")
            confirm = input("Are you sure? (type 'yes' to proceed): ").lower()
            if confirm != "yes":
                print("Operation cancelled.")
                continue

            if choice == "3":  # ADD NEW DATA
                # Use the highest existing id (not len(data)) so ids stay unique after deletions
                itemId = max((record.get("itemId", 1000000) for record in data), default=1000000) + 1

                record_restaurant_paragraph = input("Enter the new restaurant description: ")
                new_restaurant_data = new_data_entry_process(record_restaurant_paragraph, itemId)
                if new_restaurant_data:
                    data.append(new_restaurant_data)
                    save_data(data, file_path, backup_path)
                    print("✅ Restaurant added.")
                else:
                    print("❌ Could not process the description. Nothing was saved.")

            elif choice == "4":  # EDIT DATA
                record_index = read_record_index(data)
                if record_index is None:
                    continue

                updated_record = dict(data[record_index])
                for key, value in data[record_index].items():
                    if key == "itemId":  # the id must never change
                        continue
                    raw_value = input(f"{key} [{value}] (Enter to keep): ").strip()
                    if raw_value == "":
                        continue
                    try:
                        updated_record[key] = parse_field_value(key, raw_value)
                    except ValueError:
                        print(f"Invalid value for '{key}', keeping the current one.")

                # Make sure the edited record still follows the schema before saving it
                try:
                    Restaurant.model_validate(updated_record)
                except ValidationError as e:
                    print(f"❌ The edited record is not valid, nothing was saved:\n{e}")
                    continue

                data[record_index] = updated_record
                save_data(data, file_path, backup_path)
                print("✅ Record updated.")

            elif choice == "5":  # DELETE DATA
                record_index = read_record_index(data)
                if record_index is None:
                    continue

                deleted = data.pop(record_index)
                save_data(data, file_path, backup_path)
                print(f"✅ Record deleted: {deleted.get('name', 'N/A')}")

        elif choice == "6":  # EXIT
            break
        else:
            print("Invalid input.")


if __name__ == "__main__":
    manage_restaurants(FILEPATH, BACKUP_PATH)
