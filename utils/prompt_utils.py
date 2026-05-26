import re

def format_prompt(template: str, **kwargs) -> str:
    """
    Handles double curly braces {{key.subkey}} and nested dictionaries.
    """
    def resolver(match):
        # Get the content between {{ }}
        full_key = match.group(1).strip()
        keys = full_key.split('.')
        
        value = kwargs
        try:
            for k in keys:
                # Go deeper into the dictionary 
                value = value[k]
            return str(value)
        except (KeyError, TypeError):
            # If the key is not found, return the original placeholder
            return "{{" + full_key + "}}"

    # Use Regex to find the {{...}}
    pattern = r"\{\{(.*?)\}\}"
    return re.sub(pattern, resolver, template)