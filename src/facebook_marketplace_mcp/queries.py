LOCATION_SEARCH_DOC_ID = "5585904654783609"


def build_location_search_variables(query: str) -> dict:
    return {
        "params": {
            "caller": "MARKETPLACE",
            "page_category": ["CITY", "SUBCITY", "NEIGHBORHOOD"],
            "query": query,
        }
    }
