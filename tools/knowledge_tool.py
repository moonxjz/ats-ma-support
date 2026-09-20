import json
from pathlib import Path
from entity.support import SupportKnowledgeContext, KnowledgeFact

def load_product_prices_as_knowledge(json_path: str = "data/product_prices.json") -> SupportKnowledgeContext:
    """Load product prices JSON and convert to SupportKnowledgeContext."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Convert each product record into a KnowledgeFact
    answer_facts = []
    for record in data["records"]:
        # Format the fact text with product information
        fact_text = (
            f"Product: {record['title']} | "
            f"Category: {record['category']} | "
            f"SKU: {record['sku']} | "
            f"Price: {record['price']}"
        )
        
        fact = KnowledgeFact(
            text=fact_text,
            source_reference="product_prices.json"
        )
        answer_facts.append(fact)
    
    return SupportKnowledgeContext(answer_facts=answer_facts)