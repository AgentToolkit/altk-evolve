import datetime
import json
from pathlib import Path
from typing import Any

from jinja2 import Template
from litellm import completion
from pydantic import BaseModel

from altk_evolve.config.llm import llm_settings
from altk_evolve.llm.fact_extraction.categorization import CategoryManager
from altk_evolve.utils.utils import clean_llm_response


class ExtractedFact(BaseModel):
    category: str
    key: str
    value: str
    content: str


class ExtractedFacts(BaseModel):
    facts: list[str]


class CategorizedExtractedFacts(BaseModel):
    facts: list[ExtractedFact]


def _build_prompt(messages: list[dict], use_categorization: bool) -> str:
    filtered_messages = [str(message.get("content", "")) for message in messages if str(message.get("role", "")).lower() == "user"]
    messages_str = "\n".join(filtered_messages)

    prompt_input: dict[str, Any] = {
        "current_datetime": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S"),
        "user_messages": messages_str,
    }

    if use_categorization:
        category_manager = CategoryManager()
        categories_info = category_manager.get_available_categories()
        if categories_info["type"] == "predefined_only":
            categories_dict = categories_info["descriptions"]
            prompt_input["categories"] = [(k, v) for k, v in categories_dict.items()]
            prompt_file = Path(__file__).parent / "prompts/fact_extraction_predefined.jinja2"
        else:
            prompt_file = Path(__file__).parent / "prompts/fact_extraction.jinja2"
    else:
        prompt_file = Path(__file__).parent / "prompts/fact_extraction.jinja2"

    return Template(prompt_file.read_text(encoding="utf-8")).render(**prompt_input)


def extract_facts_from_messages(messages: list[dict], use_categorization: bool | None = None) -> list[str] | list[ExtractedFact]:
    """Extract user facts from chat messages."""
    if use_categorization is None:
        use_categorization = True

    prompt = _build_prompt(messages, use_categorization=use_categorization)
    last_error = None
    for _ in range(3):
        try:
            response = completion(
                model=llm_settings.fact_extraction_model,
                messages=[{"role": "user", "content": prompt}],
                custom_llm_provider=llm_settings.custom_llm_provider,
            )
            content = response.choices[0].message.content or ""  # type: ignore[union-attr]
            cleaned = clean_llm_response(content)
            parsed_json = json.loads(cleaned)
            if use_categorization:
                categorized_facts = CategorizedExtractedFacts.model_validate(parsed_json)
                return categorized_facts.facts
            extracted_facts = ExtractedFacts.model_validate(parsed_json)
            return extracted_facts.facts
        except Exception as exc:
            last_error = exc
            continue
    raise ValueError(f"Failed to parse extracted facts response: {last_error}")
