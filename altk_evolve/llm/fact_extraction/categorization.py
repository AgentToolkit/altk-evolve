from altk_evolve.config.llm import llm_settings


class CategoryManager:
    """Manage fact categorization modes and available categories."""

    PREDEFINED_CATEGORIES = {
        "personal_details": "User's personal information (name, age, location, etc.)",
        "family": "Family members and relationships",
        "professional_details": "Work, career, job-related information",
        "sports": "Sports activities, teams, fitness",
        "travel": "Travel plans, destinations, preferences",
        "food": "Food preferences, dietary restrictions, favorite cuisines",
        "music": "Music preferences, favorite artists, instruments",
        "health": "Health information, medical details, wellness",
        "technology": "Tech preferences, devices, software",
        "hobbies": "Hobbies and leisure activities",
        "fashion": "Fashion preferences, style, clothing",
        "entertainment": "Movies, TV shows, books, games",
        "milestones": "Important life events, achievements",
        "user_preferences": "General preferences and settings",
        "misc": "Anything that doesn't fit other categories",
    }

    def __init__(
        self,
        mode: str | None = None,
        allow_dynamic_categories: bool | None = None,
        confirm_new_categories: bool | None = None,
    ):
        self.mode = mode or llm_settings.categorization_mode
        self.allow_dynamic_categories = (
            allow_dynamic_categories if allow_dynamic_categories is not None else llm_settings.allow_dynamic_categories
        )
        self.confirm_new_categories = confirm_new_categories if confirm_new_categories is not None else llm_settings.confirm_new_categories
        self.custom_categories: set[str] = set()

        if self.mode not in ["predefined", "dynamic", "hybrid"]:
            raise ValueError(f"Invalid categorization mode: {self.mode}. Must be 'predefined', 'dynamic', or 'hybrid'")

    @property
    def predefined_categories(self) -> list[str]:
        return list(self.PREDEFINED_CATEGORIES.keys())

    def get_available_categories(self) -> dict:
        if self.mode == "predefined":
            return {
                "type": "predefined_only",
                "categories": self.predefined_categories,
                "descriptions": self.PREDEFINED_CATEGORIES,
            }
        if self.mode == "dynamic":
            return {"type": "dynamic", "existing_categories": list(self.custom_categories)}
        return {
            "type": "hybrid",
            "predefined": self.predefined_categories,
            "descriptions": self.PREDEFINED_CATEGORIES,
            "custom": list(self.custom_categories),
        }
